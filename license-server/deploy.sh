#!/bin/bash
# Deploy license server to VPS.
#
# Usage:
#   ./deploy.sh              # Deploy and start
#   ./deploy.sh setup        # First-time setup (install deps, create service)
#   ./deploy.sh update       # Update code and restart
#   ./deploy.sh status       # Check service status
#   ./deploy.sh logs         # View logs
#   ./deploy.sh create-key   # Create a new license key interactively

set -e

SERVER="root@79.137.198.213"
REMOTE_DIR="/opt/license-server"
SERVICE_NAME="license-server"

case "${1:-deploy}" in

setup)
    echo "=== First-time setup ==="

    # Copy files
    echo "Copying files..."
    ssh "$SERVER" "mkdir -p $REMOTE_DIR"
    scp -r main.py models.py config.py keygen.py requirements.txt setup_ssl.sh .env.example "$SERVER:$REMOTE_DIR/"

    # Install on server
    ssh "$SERVER" bash -s <<'REMOTE_SETUP'
        set -e
        cd /opt/license-server

        # Install Python + deps
        apt-get update -qq
        apt-get install -y -qq python3 python3-pip python3-venv openssl

        # Create venv
        python3 -m venv venv
        venv/bin/pip install --upgrade pip
        venv/bin/pip install -r requirements.txt

        # Generate SSL cert
        bash setup_ssl.sh

        # Create .env from example if not exists
        if [ ! -f .env ]; then
            ADMIN_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
            cat > .env <<EOF
LICENSE_ADMIN_SECRET=$ADMIN_SECRET
LICENSE_PORT=8443
EOF
            echo ""
            echo "=== Generated .env ==="
            echo "  ADMIN_SECRET: $ADMIN_SECRET"
            echo "  Save this secret! You need it for admin API calls."
            echo "======================"
        fi

        # Create systemd service
        cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=License Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/license-server
EnvironmentFile=/opt/license-server/.env
ExecStart=/opt/license-server/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8443 --ssl-certfile certs/server.crt --ssl-keyfile certs/server.key
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

        systemctl daemon-reload
        systemctl enable $SERVICE_NAME
        systemctl start $SERVICE_NAME

        # Open firewall port
        ufw allow 8443/tcp 2>/dev/null || true

        echo ""
        echo "=== Setup Complete ==="
        echo "Service status:"
        systemctl status $SERVICE_NAME --no-pager -l
REMOTE_SETUP

    echo ""
    echo "=== NEXT STEPS ==="
    echo "1. Copy the SSL certificate for pinning:"
    echo "   ssh $SERVER 'cat $REMOTE_DIR/certs/server.crt'"
    echo ""
    echo "2. Create your first license key:"
    echo "   ./deploy.sh create-key"
    ;;

update)
    echo "=== Updating ==="
    scp main.py models.py config.py keygen.py requirements.txt "$SERVER:$REMOTE_DIR/"
    ssh "$SERVER" "cd $REMOTE_DIR && venv/bin/pip install -r requirements.txt -q && systemctl restart $SERVICE_NAME"
    echo "Done. Status:"
    ssh "$SERVER" "systemctl status $SERVICE_NAME --no-pager -l"
    ;;

status)
    ssh "$SERVER" "systemctl status $SERVICE_NAME --no-pager -l"
    ;;

logs)
    ssh "$SERVER" "journalctl -u $SERVICE_NAME -n 50 --no-pager -l"
    ;;

create-key)
    read -p "Client name/email: " USER_LABEL
    read -p "Days valid [30]: " DAYS
    DAYS=${DAYS:-30}
    ssh "$SERVER" "cd $REMOTE_DIR && venv/bin/python keygen.py create --user '$USER_LABEL' --days $DAYS"
    ;;

deploy)
    echo "=== Deploying ==="
    scp main.py models.py config.py keygen.py requirements.txt "$SERVER:$REMOTE_DIR/"
    ssh "$SERVER" "systemctl restart $SERVICE_NAME"
    echo "Restarted. Status:"
    ssh "$SERVER" "systemctl status $SERVICE_NAME --no-pager -l"
    ;;

*)
    echo "Usage: $0 {setup|update|status|logs|create-key|deploy}"
    exit 1
    ;;
esac
