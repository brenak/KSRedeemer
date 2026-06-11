# Docker Build & Push Instructions

## Prerequisites
- Docker installed and running
- Docker Hub account logged in: `docker login`

## Build and Push to Docker Hub

### 1. Build the image
```bash
docker build -t brenak/kingshot-redeemer:latest .
```

### 2. Push to Docker Hub
```bash
docker push brenak/kingshot-redeemer:latest
```

## Full workflow (one command)
```bash
docker build -t brenak/kingshot-redeemer:latest . && docker push brenak/kingshot-redeemer:latest
```

## Update running container
After pushing, pull the latest image on your Oracle Cloud instance:
```bash
docker-compose pull
docker-compose up -d
```

## Verify image
Check the image exists locally:
```bash
docker images | grep kingshot-redeemer
```

## Troubleshooting

**Not logged into Docker Hub:**
```bash
docker login
# Enter your username and access token
```

**Permission denied error:**
Make sure your Docker Hub username is `brenak` or update the image name in docker-compose.yml to match your account.

**Clean rebuild (remove old image):**
```bash
docker rmi brenak/kingshot-redeemer:latest
docker build -t brenak/kingshot-redeemer:latest .
docker push brenak/kingshot-redeemer:latest
```
