# 🚀 Google Cloud / Linux Deployment Guide

This guide explains how to host your LaTeX Resume Generator on a Linux VM (like Google Cloud Compute Engine) using Docker.

## 📦 Prerequisites

1.  **Docker & Docker Compose**: Installed on your Linux VM.
2.  **Git**: To pull your code from GitHub.

## 🛠 Deployment Steps

### 1. Clone your repository
On your VM, run:
```bash
git clone https://github.com/Anup-Ojha/your-repo-name.git
cd your-repo-name
```

### 2. Build and Run using Docker Compose
The easiest way is to use the provided `docker-compose.yml`:
```bash
docker-compose up -d --build
```
This command will:
*   Download the Python parent image.
*   Install TeX Live (the Linux version of LaTeX).
*   Install Python dependencies.
*   Start the server on port `8000`.

### 3. Check Status
Verify the container is running:
```bash
docker ps
```
Check logs if needed:
```bash
docker logs -f local-resume-creator
```

---

## 🌐 Google Cloud Specifics

### Firewall Rules
Ensure port `8000` is open in your Google Cloud Console:
1.  Go to **VPC Network** > **Firewall**.
2.  Create a rule:
    *   **Targets**: All instances in network (or via tags).
    *   **Source Filter**: IP ranges (e.g., `0.0.0.0/0`).
    *   **Protocols/Ports**: TCP `8000`.

### Accessing the Web UI
Visit: `http://[YOUR_VM_EXTERNAL_IP]:8000`

---

## 📁 Persistence

We use Docker **Volumes** to ensure your data stays safe even if the container restarts:
*   `./output`: Stores all generated PDFs.
*   `./templates`: Where your resume templates live.

## 🔄 Updating the App
When you push new code to GitHub, update your VM like this:
```bash
git pull origin main
docker-compose up -d --build
```

---

## 🏗 Docker Architecture

*   **Base Image**: `python:3.10-slim-bullseye` (Lightweight and stable).
*   **LaTeX Distribution**: We use `texlive-latex-extra`. It's significantly larger than the basic installer but ensures all professional fonts and packages (like `titlesec`, `fancyhdr`) are available out of the box.
*   **Safety**: The container runs in a non-interactive mode.

---

**Made for seamless deployment on GCP by Anup Ojha**
