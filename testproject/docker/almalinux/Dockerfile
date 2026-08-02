# 検証用 AlmaLinux 9 + sshd + python3。Ansible が SSH で疎通できる最小構成。
FROM almalinux:9

ARG PUBKEY=""

RUN dnf -y install --setopt=install_weak_deps=False \
        openssh-server python3 sudo procps-ng iproute && \
    dnf clean all && \
    ssh-keygen -A && \
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
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config

EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
