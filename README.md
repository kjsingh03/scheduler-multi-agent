## README: How to Run This System

### Quick Start (Local)

**1. Clone the Repository:**

```bash
git clone <repo-url>
cd <repo-folder>
```

**2. Install Dependencies:**

```bash
pip install -r requirements.txt
```

**3. Run the System Locally:**

```bash
python main.py
```

**4. Alternate Pipeline (In Development):**

```bash
python main2.py
```

### Using Docker

### Run with Docker Compose

```bash
docker compose up --build
```

This sets up the service, mounts logs, and exposes port `8000` for any APIs or metrics.

---
