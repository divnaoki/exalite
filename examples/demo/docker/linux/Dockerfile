# 本番寄りの検証コンテナ: AlmaLinux(9-init) + systemd + sshd + python3 + ミドル。
# AlmaLinux は RHEL 互換でサブスクリプション不要のため、認証なしで pull できる。
ARG BASE_IMAGE=almalinux/9-init:latest
FROM ${BASE_IMAGE}

ARG PUBKEY=""
ARG EXTRA_PACKAGES=""
ARG ENABLE_SERVICES="sshd"

RUN dnf -y install --setopt=install_weak_deps=False \
        openssh-server python3 sudo procps-ng iproute ${EXTRA_PACKAGES} && \
    dnf clean all && \
    useradd -m -s /bin/bash ansible && \
    echo 'ansible ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ansible && \
    chmod 440 /etc/sudoers.d/ansible && \
    mkdir -p /home/ansible/.ssh && \
    printf '%s\n' "$PUBKEY" > /home/ansible/.ssh/authorized_keys && \
    chmod 700 /home/ansible/.ssh && \
    chmod 600 /home/ansible/.ssh/authorized_keys && \
    chown -R ansible:ansible /home/ansible/.ssh && \
    # root ログイン無効・パスワード認証無効（鍵のみ）
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config && \
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    # systemd により起動するサービスを有効化（オフラインでの symlink 作成）
    for svc in ${ENABLE_SERVICES}; do systemctl enable "$svc"; done

EXPOSE 22
# 9-init の CMD (/sbin/init = systemd) をそのまま使うため CMD は上書きしない。
