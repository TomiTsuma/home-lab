Below is a **clear, step-by-step, command-level explanation** of how to install **Kubernetes on Ubuntu Server** using **kubeadm**.
This is written so that:

* You understand **what each step does**
* You can repeat it for **every tenant cluster**
* It works for **MinIO + ClickHouse (stateful, private-only workloads)**

Assumptions:

* Ubuntu Server **22.04 LTS**
* At least **1 control-plane node + 1 worker node**
* You are logged in as a user with `sudo`

---

# STEP 0 — Prepare ALL Nodes (Control Plane + Workers)

You must do **everything in Step 0 on every node**.

---

## 0.1 Set hostname (important for clarity)

```bash
sudo hostnamectl set-hostname k8s-control-plane
# on worker nodes:
sudo hostnamectl set-hostname k8s-worker-1
```

---

## 0.2 Disable swap (Kubernetes requirement)

```bash
sudo swapoff -a
sudo sed -i '/ swap / s/^/#/' /etc/fstab
```

Why:

* Kubernetes memory scheduling **breaks with swap enabled**

---

## 0.3 Load required kernel modules

```bash
sudo modprobe overlay
sudo modprobe br_netfilter
```

Make persistent:

```bash
cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
```

---

## 0.4 Set required sysctl parameters

```bash
cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
```

Apply:

```bash
sudo sysctl --system
```

Why:

* Required for **pod networking and NetworkPolicies**

---

# STEP 1 — Install Container Runtime (containerd)

Kubernetes **does not run containers directly**.

---

## 1.1 Install containerd

```bash
sudo apt update
sudo apt install -y containerd
```

---

## 1.2 Configure containerd for Kubernetes

Generate default config:

```bash
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml
```

### IMPORTANT: Enable systemd cgroups

Edit config:

```bash
sudo nano /etc/containerd/config.toml
```

Set:

```toml
SystemdCgroup = true
```

Restart:

```bash
sudo systemctl restart containerd
sudo systemctl enable containerd
```

Why:

* Required for kubelet compatibility
* Prevents node instability

---

# STEP 2 — Install Kubernetes Components

You install **kubeadm, kubelet, kubectl**.

---

## 2.1 Add Kubernetes apt repository

```bash
sudo apt install -y apt-transport-https ca-certificates curl
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key \
  | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
```

```bash
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] \
https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /" \
| sudo tee /etc/apt/sources.list.d/kubernetes.list
```

---

## 2.2 Install Kubernetes binaries

```bash
sudo apt update
sudo apt install -y kubelet kubeadm kubectl
```

Lock versions:

```bash
sudo apt-mark hold kubelet kubeadm kubectl
```

Why:

* Prevents accidental upgrades breaking the cluster

---

# STEP 3 — Initialize the Control Plane (CONTROL NODE ONLY)

---

## 3.1 Initialize cluster

```bash
sudo kubeadm init \
  --pod-network-cidr=10.244.0.0/16
```

What this does:

* Bootstraps etcd
* Starts API server
* Creates cluster certificates
* Generates join token

⚠️ **SAVE the `kubeadm join` command** shown at the end.

---

## 3.2 Configure kubectl for your user

```bash
mkdir -p $HOME/.kube
sudo cp /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config
```

Test:

```bash
kubectl get nodes
```

You should see the control plane node (NotReady is OK for now).

---

# STEP 4 — Install CNI Networking (Cilium Recommended)

Kubernetes has **no networking by default**.

---

## 4.1 Install Cilium CLI

```bash
curl -L --fail --remote-name https://github.com/cilium/cilium-cli/releases/latest/download/cilium-linux-amd64.tar.gz
tar xzvf cilium-linux-amd64.tar.gz
sudo mv cilium /usr/local/bin/
```

---

## 4.2 Install Cilium

```bash
cilium install
```

Wait:

```bash
cilium status --wait
```

After this:

* Pods can talk to each other
* NetworkPolicies work

---

# STEP 5 — Join Worker Nodes

Run the **join command** from Step 3.1 **on each worker node**.

Example:

```bash
sudo kubeadm join <CONTROL_PLANE_IP>:6443 \
  --token <TOKEN> \
  --discovery-token-ca-cert-hash sha256:<HASH>
```

Verify on control plane:

```bash
kubectl get nodes
```

All nodes should be `Ready`.

---

# STEP 6 — Post-Install Hardening (MANDATORY)

---

## 6.1 Enable Pod Security Standards

```bash
kubectl label namespace default \
  pod-security.kubernetes.io/enforce=restricted
```

Repeat for all namespaces later.

---

## 6.2 Create Default Deny NetworkPolicy

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny
  namespace: default
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
```

Apply:

```bash
kubectl apply -f default-deny.yaml
```

---

# STEP 7 — Verify Cluster Health

Run:

```bash
kubectl get nodes
kubectl get pods -A
```

All system pods should be `Running`.

---

# ✅ WHAT YOU HAVE NOW

You now have:

✔ Kubernetes cluster on Ubuntu
✔ containerd runtime
✔ Secure control plane
✔ NetworkPolicies enabled
✔ Ready for MinIO & ClickHouse operators

This is a **production-correct foundation**.

---