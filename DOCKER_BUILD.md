# Docker Build & Push Instructions

## Prerequisites
- Docker installed and running
- Docker Hub account logged in: `docker login`

## Build and Push to Docker Hub

Run these from the repo root, on your Windows dev machine (PowerShell).

### 1. Build the image
```powershell
docker build -t brenak/kingshot-redeemer:latest .
```

### 2. Push to Docker Hub
```powershell
docker push brenak/kingshot-redeemer:latest
```

## Full workflow (one command)

PowerShell doesn't support `&&`. Use `if ($?) { }` to only push when the build succeeds:
```powershell
docker build -t brenak/kingshot-redeemer:latest .
if ($?) { docker push brenak/kingshot-redeemer:latest }
```

(If you're running these from a bash shell instead, `&&` works as usual:
`docker build -t brenak/kingshot-redeemer:latest . && docker push brenak/kingshot-redeemer:latest`)

## Update running container
After pushing, pull the latest image on your Oracle Cloud instance (bash):
```bash
docker compose pull
docker compose up -d
```

(`docker compose`, no hyphen — the Compose plugin bundled with modern Docker installs.
The old standalone `docker-compose` binary isn't installed on the server and isn't needed.)

## Verify image
Check the image exists locally.

PowerShell:
```powershell
docker images | Select-String kingshot-redeemer
```

bash:
```bash
docker images | grep kingshot-redeemer
```

## Troubleshooting

**Not logged into Docker Hub:**
```powershell
docker login
# Enter your username and access token
```

**Permission denied error:**
Make sure your Docker Hub username is `brenak` or update the image name in docker-compose.yml to match your account.

**Clean rebuild (remove old image):**
```powershell
docker rmi brenak/kingshot-redeemer:latest
docker build -t brenak/kingshot-redeemer:latest .
if ($?) { docker push brenak/kingshot-redeemer:latest }
```
