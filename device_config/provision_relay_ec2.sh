#!/usr/bin/env bash

set -euo pipefail

PROFILE="${PROFILE:-matterhub-relay}"
REGION="${REGION:-ap-northeast-2}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t4g.nano}"
INSTANCE_NAME="${INSTANCE_NAME:-matterhub-relay}"
SECURITY_GROUP_NAME="${SECURITY_GROUP_NAME:-matterhub-relay-sg}"
KEY_NAME="${KEY_NAME:-matterhub-relay-operator-key}"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/${KEY_NAME}.pem}"
SSH_PORT="${SSH_PORT:-443}"
ALLOCATE_EIP="${ALLOCATE_EIP:-1}"
PORT_RANGE_START="${PORT_RANGE_START:-22000}"
PORT_RANGE_END="${PORT_RANGE_END:-23999}"
OUTPUT_JSON="${OUTPUT_JSON:-device_config/relay/provision-output.json}"
DRY_RUN=0

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log() {
  printf '[relay-provision] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage: ./device_config/provision_relay_ec2.sh [options]

Options:
  --profile <name>              AWS profile (default: matterhub-relay)
  --region <name>               AWS region (default: ap-northeast-2)
  --instance-type <type>        EC2 type (default: t4g.nano)
  --instance-name <name>        EC2 Name tag (default: matterhub-relay)
  --security-group <name>       Security Group name (default: matterhub-relay-sg)
  --key-name <name>             EC2 key pair name (default: matterhub-relay-operator-key)
  --key-path <path>             Local PEM output path (default: ~/.ssh/<key-name>.pem)
  --ssh-port <port>             Relay SSH port (default: 443)
  --output-json <path>          Output file path (default: device_config/relay/provision-output.json)
  --dry-run                     Print execution plan only
  -h, --help                    Show help

Notes:
  - Relay server is prepared for high connection count by reserving hub port range metadata:
    PORT_RANGE_START..PORT_RANGE_END (default: 22000-23999 for 2,000 hubs).
  - For >100 hubs, scale up instance type later without architecture changes.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --instance-type)
      INSTANCE_TYPE="$2"
      shift 2
      ;;
    --instance-name)
      INSTANCE_NAME="$2"
      shift 2
      ;;
    --security-group)
      SECURITY_GROUP_NAME="$2"
      shift 2
      ;;
    --key-name)
      KEY_NAME="$2"
      shift 2
      ;;
    --key-path)
      KEY_PATH="$2"
      shift 2
      ;;
    --ssh-port)
      SSH_PORT="$2"
      shift 2
      ;;
    --output-json)
      OUTPUT_JSON="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ "$DRY_RUN" -ne 1 ]; then
  if ! command -v aws >/dev/null 2>&1; then
    echo "aws CLI is required." >&2
    exit 1
  fi

  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required." >&2
    exit 1
  fi
fi

AWS_BASE=(aws --profile "$PROFILE" --region "$REGION")

run_aws() {
  "${AWS_BASE[@]}" "$@"
}

if [ "$DRY_RUN" -eq 1 ]; then
  log "dry-run enabled"
fi

log "profile=$PROFILE region=$REGION"
log "instance_name=$INSTANCE_NAME instance_type=$INSTANCE_TYPE"
log "security_group=$SECURITY_GROUP_NAME key_name=$KEY_NAME ssh_port=$SSH_PORT"
log "hub_port_range=${PORT_RANGE_START}-${PORT_RANGE_END}"

if [ "$DRY_RUN" -eq 1 ]; then
  log "plan: validate profile identity"
else
  IDENTITY_JSON="$(run_aws sts get-caller-identity)"
  ACCOUNT_ID="$(echo "$IDENTITY_JSON" | jq -r '.Account')"
  ARN="$(echo "$IDENTITY_JSON" | jq -r '.Arn')"
  log "caller_account=$ACCOUNT_ID caller_arn=$ARN"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log "plan: resolve default VPC/subnet"
  log "plan: create or reuse security group and open tcp/${SSH_PORT}"
  log "plan: create or reuse operator key pair and save ${KEY_PATH}"
  log "plan: fetch latest AL2023 ARM64 AMI"
  log "plan: create or reuse EC2 instance with bootstrap user-data"
  log "plan: allocate/associate Elastic IP"
  exit 0
fi

VPC_ID="$(run_aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)"
if [ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ]; then
  echo "Default VPC not found in region ${REGION}." >&2
  exit 1
fi
log "vpc_id=$VPC_ID"

SUBNET_ID="$(run_aws ec2 describe-subnets \
  --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true \
  --query 'Subnets[0].SubnetId' --output text)"
if [ "$SUBNET_ID" = "None" ] || [ -z "$SUBNET_ID" ]; then
  SUBNET_ID="$(run_aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" --query 'Subnets[0].SubnetId' --output text)"
fi
if [ "$SUBNET_ID" = "None" ] || [ -z "$SUBNET_ID" ]; then
  echo "No subnet found in VPC ${VPC_ID}." >&2
  exit 1
fi
log "subnet_id=$SUBNET_ID"

SG_ID="$(run_aws ec2 describe-security-groups \
  --filters Name=vpc-id,Values="$VPC_ID" Name=group-name,Values="$SECURITY_GROUP_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"

if [ -z "$SG_ID" ] || [ "$SG_ID" = "None" ]; then
  SG_ID="$(run_aws ec2 create-security-group \
    --group-name "$SECURITY_GROUP_NAME" \
    --description "MatterHub relay SSH ingress (${SSH_PORT})" \
    --vpc-id "$VPC_ID" \
    --query 'GroupId' --output text)"
  log "created_security_group=$SG_ID"
else
  log "reused_security_group=$SG_ID"
fi

if ! run_aws ec2 describe-security-groups --group-ids "$SG_ID" \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`${SSH_PORT}\` && ToPort==\`${SSH_PORT}\` && IpProtocol=='tcp'] | length(@)" \
  --output text | grep -q '^[1-9]'; then
  run_aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --ip-permissions "IpProtocol=tcp,FromPort=${SSH_PORT},ToPort=${SSH_PORT},IpRanges=[{CidrIp=0.0.0.0/0,Description='MatterHub relay SSH'}],Ipv6Ranges=[{CidrIpv6=::/0,Description='MatterHub relay SSH v6'}]" \
    >/dev/null
  log "opened_ingress_tcp_${SSH_PORT}=true"
else
  log "ingress_tcp_${SSH_PORT}=already_open"
fi

if run_aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  log "reused_key_pair=$KEY_NAME"
  if [ ! -f "$KEY_PATH" ]; then
    log "warning: key pair exists in AWS but local pem not found at $KEY_PATH"
  fi
else
  mkdir -p "$(dirname "$KEY_PATH")"
  run_aws ec2 create-key-pair --key-name "$KEY_NAME" --query 'KeyMaterial' --output text > "$KEY_PATH"
  chmod 600 "$KEY_PATH"
  log "created_key_pair=$KEY_NAME saved_pem=$KEY_PATH"
fi

AMI_ID="$(run_aws ssm get-parameter \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --query 'Parameter.Value' --output text)"
log "ami_id=$AMI_ID"

USER_DATA_FILE="$(mktemp)"
cat > "$USER_DATA_FILE" <<EOF
#!/bin/bash
set -euxo pipefail

dnf -y update || true
dnf -y install jq

id -u whatsmatter >/dev/null 2>&1 || useradd -m -s /bin/bash whatsmatter
mkdir -p /home/whatsmatter/.ssh
touch /home/whatsmatter/.ssh/authorized_keys
chmod 700 /home/whatsmatter/.ssh
chmod 600 /home/whatsmatter/.ssh/authorized_keys
chown -R whatsmatter:whatsmatter /home/whatsmatter/.ssh

if [ ! -f /home/ec2-user/.ssh/hub_access_ed25519 ]; then
  sudo -u ec2-user ssh-keygen -t ed25519 -N "" -C "relay-hub-access@$(hostname)" -f /home/ec2-user/.ssh/hub_access_ed25519
fi
chmod 600 /home/ec2-user/.ssh/hub_access_ed25519
chmod 644 /home/ec2-user/.ssh/hub_access_ed25519.pub
chown ec2-user:ec2-user /home/ec2-user/.ssh/hub_access_ed25519 /home/ec2-user/.ssh/hub_access_ed25519.pub

mkdir -p /opt/matterhub-relay
touch /opt/matterhub-relay/hubs.map
chmod 664 /opt/matterhub-relay/hubs.map

cat >/usr/local/bin/j <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
MAP_FILE="/opt/matterhub-relay/hubs.map"

if [ \$# -lt 1 ]; then
  echo "usage: j <hub_id_or_keyword> [remote_command ...]"
  exit 1
fi

hub_query="\$1"
shift
if [ ! -f "\$MAP_FILE" ]; then
  echo "hub map file not found: \$MAP_FILE"
  exit 1
fi

line="\$(awk -v q="\$hub_query" '\$1 == q {print \$0}' "\$MAP_FILE" | tail -n 1)"
if [ -z "\$line" ]; then
  match_count="\$(awk -v q="\$hub_query" '\$1 ~ q {count+=1; last=\$0} END{print count":"last}' "\$MAP_FILE")"
  count="\${match_count%%:*}"
  last="\${match_count#*:}"
  if [ "\$count" = "1" ] && [ -n "\$last" ]; then
    line="\$last"
  elif [ "\$count" = "0" ]; then
    echo "hub not found: \$hub_query"
    exit 1
  else
    echo "multiple hubs matched '\$hub_query'."
    awk -v q="\$hub_query" '\$1 ~ q {print \$1" -> "$2" ("$3")"}' "\$MAP_FILE"
    exit 1
  fi
fi

if [ -z "\$line" ]; then
  echo "hub not found: \$hub_query"
  exit 1
fi

port="\$(echo "\$line" | awk '{print \$2}')"
device_user="\$(echo "\$line" | awk '{print \$3}')"
if [ -z "\$device_user" ]; then
  device_user="whatsmatter"
fi

if [ \$# -gt 0 ]; then
  exec ssh -o StrictHostKeyChecking=no -o BatchMode=yes -i /home/ec2-user/.ssh/hub_access_ed25519 -p "\$port" "\${device_user}@127.0.0.1" "\$@"
fi

exec ssh -o StrictHostKeyChecking=no -o BatchMode=yes -i /home/ec2-user/.ssh/hub_access_ed25519 -p "\$port" "\${device_user}@127.0.0.1"
EOS

cat >/usr/local/bin/register-hub <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
MAP_FILE="/opt/matterhub-relay/hubs.map"

if [ \$# -lt 2 ]; then
  echo "usage: register-hub <hub_id> <port> [device_user]"
  exit 1
fi

hub_id="\$1"
port="\$2"
device_user="\${3:-whatsmatter}"

tmp="\$(mktemp)"
if [ -f "\$MAP_FILE" ]; then
  awk -v hub="\$hub_id" '\$1 != hub {print \$0}' "\$MAP_FILE" > "\$tmp"
fi
printf '%s %s %s\n' "\$hub_id" "\$port" "\$device_user" >> "\$tmp"
install -m 664 "\$tmp" "\$MAP_FILE"
rm -f "\$tmp"
echo "registered hub=\$hub_id port=\$port user=\$device_user"
EOS

chmod +x /usr/local/bin/j /usr/local/bin/register-hub

SSHD_CONF="/etc/ssh/sshd_config"
if ! grep -q '^Port ${SSH_PORT}$' "\$SSHD_CONF"; then
  echo "Port ${SSH_PORT}" >> "\$SSHD_CONF"
fi
if grep -q '^#\\?PasswordAuthentication ' "\$SSHD_CONF"; then
  sed -i 's/^#\\?PasswordAuthentication .*/PasswordAuthentication no/' "\$SSHD_CONF"
else
  echo "PasswordAuthentication no" >> "\$SSHD_CONF"
fi
if grep -q '^#\\?PubkeyAuthentication ' "\$SSHD_CONF"; then
  sed -i 's/^#\\?PubkeyAuthentication .*/PubkeyAuthentication yes/' "\$SSHD_CONF"
else
  echo "PubkeyAuthentication yes" >> "\$SSHD_CONF"
fi
if grep -q '^#\\?PermitRootLogin ' "\$SSHD_CONF"; then
  sed -i 's/^#\\?PermitRootLogin .*/PermitRootLogin no/' "\$SSHD_CONF"
else
  echo "PermitRootLogin no" >> "\$SSHD_CONF"
fi
if grep -q '^#\\?AllowTcpForwarding ' "\$SSHD_CONF"; then
  sed -i 's/^#\\?AllowTcpForwarding .*/AllowTcpForwarding yes/' "\$SSHD_CONF"
else
  echo "AllowTcpForwarding yes" >> "\$SSHD_CONF"
fi
if grep -q '^#\\?GatewayPorts ' "\$SSHD_CONF"; then
  sed -i 's/^#\\?GatewayPorts .*/GatewayPorts no/' "\$SSHD_CONF"
else
  echo "GatewayPorts no" >> "\$SSHD_CONF"
fi
if grep -q '^#\\?ClientAliveInterval ' "\$SSHD_CONF"; then
  sed -i 's/^#\\?ClientAliveInterval .*/ClientAliveInterval 60/' "\$SSHD_CONF"
else
  echo "ClientAliveInterval 60" >> "\$SSHD_CONF"
fi
if grep -q '^#\\?ClientAliveCountMax ' "\$SSHD_CONF"; then
  sed -i 's/^#\\?ClientAliveCountMax .*/ClientAliveCountMax 3/' "\$SSHD_CONF"
else
  echo "ClientAliveCountMax 3" >> "\$SSHD_CONF"
fi

systemctl enable sshd
systemctl restart sshd
EOF

INSTANCE_ID="$(run_aws ec2 describe-instances \
  --filters Name=tag:Name,Values="$INSTANCE_NAME" Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[0].InstanceId' --output text 2>/dev/null || true)"

if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
  log "reused_instance=$INSTANCE_ID"
  INSTANCE_STATE="$(run_aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --query 'Reservations[0].Instances[0].State.Name' --output text)"
  if [ "$INSTANCE_STATE" = "stopped" ]; then
    run_aws ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null
    log "started_instance=$INSTANCE_ID"
  fi
else
  INSTANCE_ID="$(run_aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --subnet-id "$SUBNET_ID" \
    --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=8,VolumeType=gp3,DeleteOnTermination=true}' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${INSTANCE_NAME}},{Key=Project,Value=MatterHubRelay},{Key=ManagedBy,Value=codex}]" \
    --user-data "file://${USER_DATA_FILE}" \
    --query 'Instances[0].InstanceId' --output text)"
  log "created_instance=$INSTANCE_ID"
fi

run_aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
log "instance_running=$INSTANCE_ID"

PUBLIC_IP="$(run_aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
PUBLIC_DNS="$(run_aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --query 'Reservations[0].Instances[0].PublicDnsName' --output text)"

EIP_PUBLIC_IP=""
EIP_ALLOC_ID=""
if [ "$ALLOCATE_EIP" = "1" ]; then
  EIP_ALLOC_ID="$(run_aws ec2 describe-addresses \
    --filters Name=instance-id,Values="$INSTANCE_ID" \
    --query 'Addresses[0].AllocationId' --output text 2>/dev/null || true)"
  if [ -z "$EIP_ALLOC_ID" ] || [ "$EIP_ALLOC_ID" = "None" ]; then
    EIP_ALLOC_ID="$(run_aws ec2 allocate-address --domain vpc --query 'AllocationId' --output text)"
    run_aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC_ID" >/dev/null
    log "allocated_and_associated_eip=$EIP_ALLOC_ID"
  else
    log "reused_associated_eip=$EIP_ALLOC_ID"
  fi
  EIP_PUBLIC_IP="$(run_aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC_ID" --query 'Addresses[0].PublicIp' --output text)"
fi

HOST_FOR_TUNNEL="$PUBLIC_DNS"
if [ -n "$EIP_PUBLIC_IP" ] && [ "$EIP_PUBLIC_IP" != "None" ]; then
  HOST_FOR_TUNNEL="$EIP_PUBLIC_IP"
fi

mkdir -p "$(dirname "$PROJECT_ROOT/$OUTPUT_JSON")"
cat > "$PROJECT_ROOT/$OUTPUT_JSON" <<EOF
{
  "profile": "${PROFILE}",
  "region": "${REGION}",
  "instance_name": "${INSTANCE_NAME}",
  "instance_id": "${INSTANCE_ID}",
  "instance_type": "${INSTANCE_TYPE}",
  "security_group_id": "${SG_ID}",
  "ssh_port": ${SSH_PORT},
  "key_name": "${KEY_NAME}",
  "key_path": "${KEY_PATH}",
  "public_ip": "${PUBLIC_IP}",
  "public_dns": "${PUBLIC_DNS}",
  "elastic_ip": "${EIP_PUBLIC_IP}",
  "elastic_ip_allocation_id": "${EIP_ALLOC_ID}",
  "host_for_tunnel": "${HOST_FOR_TUNNEL}",
  "hub_port_range_start": ${PORT_RANGE_START},
  "hub_port_range_end": ${PORT_RANGE_END}
}
EOF

log "output_json=$PROJECT_ROOT/$OUTPUT_JSON"
log "relay_host_for_device=$HOST_FOR_TUNNEL"
log "operator_connect=ssh -i $KEY_PATH -p ${SSH_PORT} ec2-user@${HOST_FOR_TUNNEL}"
log "relay_hub_access_pubkey_fetch=ssh -i $KEY_PATH -p ${SSH_PORT} ec2-user@${HOST_FOR_TUNNEL} 'cat /home/ec2-user/.ssh/hub_access_ed25519.pub'"
log "register_hub_on_relay=register-hub <hub_id> <remote_port> [device_user]"
log "hub_authorized_key_target=/home/whatsmatter/.ssh/authorized_keys"
log "shortcut_usage_on_relay=j <hub_id>"

rm -f "$USER_DATA_FILE"
