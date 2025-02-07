from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import subprocess

app = Flask(__name__)
app.secret_key = 'your-secret-key'


app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
\
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)

class Namespace(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('namespaces', lazy=True))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            return jsonify({"message": "Username and password are required"}), 400

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            return jsonify({"message": "Username already exists"}), 400

        new_user = User(username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username, password=password).first()
        if user:
            login_user(user)
            return redirect(url_for('index'))
        else:
            return jsonify({"message": "Invalid username or password"}), 401

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/create-namespace', methods=['POST'])
@login_required
def create_namespace():
    namespace_name = request.form.get('namespace')
    if not namespace_name:
        return jsonify({'message': 'Namespace name is required'}), 400

    existing_namespace = Namespace.query.filter_by(name=namespace_name, user_id=current_user.id).first()
    if existing_namespace:
        return jsonify({'message': 'Namespace already exists for this user'}), 400

    namespace = Namespace(name=namespace_name, user_id=current_user.id)
    db.session.add(namespace)
    db.session.commit()

    result = subprocess.run(['kubectl', 'create', 'namespace', namespace_name], capture_output=True, text=True)
    if result.returncode == 0:
        return jsonify({'message': f'Namespace {namespace_name} created successfully!'})
    else:
        return jsonify({'message': result.stderr}), 400

@app.route('/delete-namespace', methods=['POST'])
@login_required
def delete_namespace():
    namespace_name = request.form.get('namespace')
    if not namespace_name:
        return jsonify({'message': 'Namespace name is required'}), 400

    namespace = Namespace.query.filter_by(name=namespace_name, user_id=current_user.id).first()
    if not namespace:
        return jsonify({'message': 'Unauthorized to delete this namespace'}), 403

    db.session.delete(namespace)
    db.session.commit()

    result = subprocess.run(['kubectl', 'delete', 'namespace', namespace_name], capture_output=True, text=True)
    if result.returncode == 0:
        return jsonify({'message': f'Namespace {namespace_name} deleted successfully!'})
    else:
        return jsonify({'message': result.stderr}), 400

@app.route('/list-pods', methods=['GET'])
@login_required
def list_pods():
    namespace_name = request.args.get('namespace')
    if not namespace_name:
        return jsonify({'message': 'Namespace is required'}), 400

    namespace = Namespace.query.filter_by(name=namespace_name, user_id=current_user.id).first()
    if not namespace:
        return jsonify({'message': 'Unauthorized to list pods in this namespace'}), 403

    result = subprocess.run(['kubectl', 'get', 'pods', '-n', namespace_name, '-o', 'name'], capture_output=True, text=True)
    if result.returncode == 0:
        pods = result.stdout.splitlines()
        return jsonify({'pods': pods})
    else:
        return jsonify({'message': result.stderr}), 400

@app.route('/create-deployment', methods=['POST'])
@login_required
def create_deployment():
    namespace_name = request.form.get('namespace')
    image = request.form.get('image')
    port = int(request.form.get('port'))
    deployment_name = request.form.get('deployment_name', 'custom-deployment')

    if not namespace_name or not image or not port:
        return jsonify({'message': 'Namespace, Image, and Port are required'}), 400

    namespace = Namespace.query.filter_by(name=namespace_name, user_id=current_user.id).first()
    if not namespace:
        return jsonify({'message': 'Unauthorized to create deployment in this namespace'}), 403

    deployment_yaml = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {deployment_name}
  namespace: {namespace_name}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {deployment_name}
  template:
    metadata:
      labels:
        app: {deployment_name}
    spec:
      containers:
      - name: {deployment_name}-container
        image: {image}
        ports:
        - containerPort: {port}
    """

    node_port = max(30000, min(32767, 30000 + (port % 2768)))
    service_yaml = f"""
apiVersion: v1
kind: Service
metadata:
  name: {deployment_name}-service
  namespace: {namespace_name}
spec:
  selector:
    app: {deployment_name}
  type: NodePort
  ports:
  - protocol: TCP
    port: {port}
    targetPort: {port}
    nodePort: {node_port}
    """

    with open('/tmp/deployment.yaml', 'w') as f:
        f.write(deployment_yaml)

    with open('/tmp/service.yaml', 'w') as f:
        f.write(service_yaml)

    deploy_result = subprocess.run(['kubectl', 'apply', '-f', '/tmp/deployment.yaml'], capture_output=True, text=True)
    service_result = subprocess.run(['kubectl', 'apply', '-f', '/tmp/service.yaml'], capture_output=True, text=True)

    if deploy_result.returncode == 0 and service_result.returncode == 0:
        return jsonify({'message': f'Deployment and Service created successfully! Access via NodePort {node_port}.'})
    else:
        return jsonify({'message': deploy_result.stderr + service_result.stderr}), 400

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)
