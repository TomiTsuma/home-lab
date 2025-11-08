Perfect 👍
Here’s a production-grade Markdown guide for **setting up and running distributed training with PyTorch DDP** across **two identical nodes** (each with GPUs).

---

````markdown
# Distributed Training Setup (PyTorch DDP on Two Nodes)

This guide explains how to train your PyTorch model in distributed mode using **DistributedDataParallel (DDP)** across **two machines (nodes)** with the same specs.

---

## 🧩 Prerequisites

### Hardware
- Two nodes (machines) with identical specs.
- Each node has ≥1 GPU (e.g., NVIDIA RTX 3060, 11GB VRAM).
- Both nodes connected on the same network (LAN or via VPN).

### Software
- Ubuntu 20.04 or later
- Python 3.10+  
- PyTorch (with CUDA support)
- NCCL backend installed (comes with PyTorch CUDA builds)
- SSH access between nodes (passwordless preferred)
- The same code, dataset path, and environment setup on both nodes

---

## ⚙️ Step 1: Set Up Environment

On **both nodes**, run:

```bash
# Update system and install dependencies
sudo apt update && sudo apt install -y python3-pip git

# Install CUDA-compatible PyTorch
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# (Optional) Install additional tools
pip install tqdm numpy transformers
````

Verify CUDA:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

---

## 🔑 Step 2: Enable SSH Access Between Nodes

On Node 1 (the master node - rhadamanthys-dl):

```bash
ssh-keygen -t rsa
ssh-copy-id rhadamanthys@192.168.1.108
```

Test connection:

```bash
ssh aeacus@192.168.1.115
```

---

## 🧠 Step 3: Configure Node Roles

| Node   | Role   | IP Address |
| ------ | ------ | ------------------ |
| Node 1 | Master | 192.168.1.108       |
| Node 2 | Worker | 192.168.1.115       |

---

## 🚀 Step 4: Modify Your Training Script

Wrap your model training code with DDP logic.
Here’s a minimal example (`train_ddp.py`):

```python
import os
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP

def setup():
    dist.init_process_group(
        backend="nccl",            # Use NCCL for GPU communication
        init_method="env://"
    )
    torch.cuda.set_device(int(os.environ["LOCAL_RANK"]))

def cleanup():
    dist.destroy_process_group()

def main():
    setup()

    model = MyModel().cuda()
    ddp_model = DDP(model, device_ids=[int(os.environ["LOCAL_RANK"])])

    optimizer = torch.optim.Adam(ddp_model.parameters(), lr=1e-4)
    dataset = MyDataset()
    sampler = torch.utils.data.distributed.DistributedSampler(dataset)
    loader = torch.utils.data.DataLoader(dataset, batch_size=8, sampler=sampler)

    for epoch in range(10):
        sampler.set_epoch(epoch)
        for batch in loader:
            optimizer.zero_grad()
            loss = ddp_model(batch).mean()
            loss.backward()
            optimizer.step()
        if dist.get_rank() == 0:
            print(f"Epoch {epoch} complete")

    cleanup()

if __name__ == "__main__":
    main()
```

---

## 🧩 Step 5: Launch Distributed Training

Use `torchrun` to start training.

### On Node 1 (Master):

```bash
torchrun \
  --nnodes=2 \
  --nproc_per_node=1 \
  --node_rank=0 \
  --master_addr="192.168.1.108" \
  --master_port=2095 \
  fine_tune_instruct_ddp.py
```

### On Node 2 (Worker):

```bash
torchrun \
  --nnodes=2 \
  --nproc_per_node=1 \
  --node_rank=1 \
  --master_addr="192.168.1.108" \
  --master_port=2095 \
  fine_tune_instruct_ddp.py
```

---

## 🧹 Step 6: Troubleshooting

**Common Issues:**

| Error                                | Fix                                                                    |
| ------------------------------------ | ---------------------------------------------------------------------- |
| `NCCL error: unhandled system error` | Ensure both nodes can ping each other and have matching CUDA versions. |
| `Connection refused`                 | Check firewall (`sudo ufw disable`) or incorrect `master_addr`.        |
| `CUDA out of memory`                 | Lower `batch_size` or enable gradient checkpointing.                   |
| `timeout waiting for rank`           | Make sure both commands are launched almost simultaneously.            |

---

## 🧾 Step 7: Optional Config

If you have multiple GPUs per node, increase `--nproc_per_node` accordingly.
Example for 2 GPUs per node:

```bash
torchrun --nnodes=2 --nproc_per_node=2 --node_rank=0 ...
```

---

## ✅ Verification

When training starts successfully, you should see logs similar to:

```
[Rank 0] Epoch 0 complete
[Rank 1] Epoch 0 complete
[Rank 0] Epoch 1 complete
...
```

---

## 📚 References

* [PyTorch DDP Documentation](https://pytorch.org/docs/stable/notes/ddp.html)
* [NCCL Backend Setup](https://pytorch.org/docs/stable/distributed.html#backends)
* [torchrun CLI](https://pytorch.org/docs/stable/elastic/run.html)

---

```

---

Would you like me to **include your existing model training code** (from `fine_tune_instruct.py`) inside this `.md` file as an example DDP version?  
That way, it will be ready to run directly on both nodes.
```
