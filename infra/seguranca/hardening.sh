#!/bin/bash
# Endurecimento da VPS: SSH só por chave, firewall UFW e fail2ban. Pode rodar de novo (idempotente).
# Na VPS, a partir do seu usuário (não do root):  sudo bash infra/seguranca/hardening.sh
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "rode com sudo"; exit 1; }
USUARIO="${SUDO_USER:?rode com sudo a partir do seu usuário, não como root}"
# Trava de segurança: sem chave cadastrada, desligar a senha trancaria você fora da VPS.
[ -s "/home/$USUARIO/.ssh/authorized_keys" ] || { echo "sem chave em /home/$USUARIO/.ssh/authorized_keys; abortado"; exit 1; }
PORTA_SSH=$(sshd -T | awk '$1 == "port" { print $2; exit }')

echo "== 1. SSH só por chave (porta $PORTA_SSH)"
# 00-: no Ubuntu vale o primeiro valor lido, e este vence o 50-cloud-init.conf da Hostinger.
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
MaxAuthTries 3
EOF
sshd -t # config inválida: para aqui, antes do reload
systemctl reload ssh

echo "== 2. UFW: nega entrada, libera só SSH, HTTP e HTTPS"
apt-get install -y -qq ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow "$PORTA_SSH/tcp" comment ssh
ufw allow 80/tcp comment http
ufw allow 443/tcp comment https
ufw --force enable

echo "== 3. fail2ban: bane por 1 h quem errar o login SSH 5 vezes em 10 min"
apt-get install -y -qq fail2ban
cat > /etc/fail2ban/jail.local <<EOF
[sshd]
enabled  = true
port     = $PORTA_SSH
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 1h
EOF
systemctl enable fail2ban
systemctl restart fail2ban

echo "== Conferência"
sshd -T | grep -Ei '^(passwordauthentication|permitrootlogin|pubkeyauthentication) '
ufw status verbose
sleep 2 && fail2ban-client status sshd
