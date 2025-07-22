# Targeting Tools

## 1. Server Preparation

### Update packages

```bash
sudo apt update && sudo apt upgrade -y
```

### Install system dependencies

```bash
sudo apt install nginx postgresql postgresql-contrib python3-pip build-essential libpq-dev -y
```

---

## 2. Setup PostgreSQL

### Access PostgreSQL

```bash
sudo -u postgres psql
```

### Create database and user

```sql
CREATE DATABASE your_db_name;
CREATE USER your_db_user WITH PASSWORD 'your_password';
ALTER ROLE your_db_user SET client_encoding TO 'utf8';
ALTER ROLE your_db_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE your_db_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE your_db_name TO your_db_user;
\q
```

Update your `data.json`:

```python
{
    "base_directory": "",
    "SECRET_KEY": "",
    "ALLOWED_HOSTS": ["localhost",
    "127.0.0.1"],
    "CSRF_TRUSTED_ORIGINS": [
        "http://localhost:8000",
        "http://127.0.0.1:8000"
    ],
    "postgres_db": "",
    "postgres_user": "",
    "postgres_pass": "",
    "postgres_host": "",
}
```

---

## 3. Setup Miniforge Environment

### Install Miniforge (if not already installed)

```bash
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash Miniforge3-Linux-x86_64.sh
```

Restart your shell or source the conda profile:

```bash
source ~/.bashrc
```

### Create environment from `targeting_environment.yml`

```bash
conda env create -f targeting_environment.yml
conda activate targeting
```

---

## 4. Django Application Setup

### Collect static files

```bash
python manage.py collectstatic
```

### Run migrations

```bash
python manage.py migrate
```

---

### Create Super User if necessary

```bash
python manage.py createsuperuser
```

---

## 5. Gunicorn Setup

### Install Gunicorn in the Conda env

```bash
pip install gunicorn
```

### Create systemd service for Gunicorn

```bash
sudo nano /etc/systemd/system/gunicorn.service
```

```ini
[Unit]
Description=gunicorn daemon
After=network.target

[Service]
User=your_user
Group=www-data
WorkingDirectory=/home/your_user/yourproject
ExecStart=/home/your_user/miniforge3/envs/targeting/bin/gunicorn --access-logfile - --workers 3 --bind unix:/home/your_user/yourproject/gunicorn.sock targeting_project.wsgi:application

[Install]
WantedBy=multi-user.target
```

### Start and enable Gunicorn

```bash
sudo systemctl start gunicorn
sudo systemctl enable gunicorn
```

---

## 6. Nginx Configuration

### Create site config

```bash
sudo nano /etc/nginx/sites-available/yourproject
```

```nginx
server {
    listen 80;
    server_name your.server.ip.or.domain;

    location = /favicon.ico { access_log off; log_not_found off; }
    location /static/ {
        root /home/your_user/yourproject;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/home/your_user/yourproject/gunicorn.sock;
    }
}
```

### Enable the config

```bash
sudo ln -s /etc/nginx/sites-available/yourproject /etc/nginx/sites-enabled
sudo nginx -t
sudo systemctl restart nginx
```
