# Docker Setup Guide

Complete guide for setting up automated Docker builds and publishing to Docker Hub.

## Prerequisites

1. **Docker Hub Account**: Sign up at https://hub.docker.com if you don't have one
2. **GitHub Repository**: Your spool-sync repository
3. **GitHub Account**: With admin access to the repository

## Step 1: Create Docker Hub Access Token

1. Go to https://hub.docker.com/settings/security
2. Click **"New Access Token"**
3. Name: `github-actions-spoolsync`
4. Permissions: **"Read, Write, Delete"**
5. Click **"Generate"**
6. **Copy the token immediately** (you won't see it again!)

## Step 2: Configure GitHub Secrets

1. Go to your GitHub repository
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **"New repository secret"**
4. Add two secrets:

### Secret 1: DOCKER_HUB_USERNAME
- **Name**: `DOCKER_HUB_USERNAME`
- **Value**: Your Docker Hub username (e.g., `johndoe`)

### Secret 2: DOCKER_HUB_TOKEN
- **Name**: `DOCKER_HUB_TOKEN`
- **Value**: The access token you copied in Step 1

## Step 3: Update Workflow File (Optional)

The workflow is already configured, but you may want to customize:

1. Edit `.github/workflows/docker-publish.yml`
2. Change `IMAGE_NAME` if you want a different name (default: `spoolsync`)

## Step 4: Trigger First Build

### Option A: Push to main branch

```bash
git add .
git commit -m "Add GitHub Actions workflow for Docker builds"
git push origin main
```

This will build and push with the `latest` tag.

### Option B: Create a version tag

```bash
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0
```

This will build and push with tags: `1.0.0`, `1.0`, `1`, and `latest`.

## Step 5: Verify Build

1. Go to your GitHub repository
2. Click the **"Actions"** tab
3. Watch the "Build and Push Docker Image" workflow
4. Once complete, check Docker Hub: `https://hub.docker.com/r/YOUR_USERNAME/spoolsync`

## Usage

### Pull and Run

```bash
# Pull latest version
docker pull etzion/spoolsync:latest

# Or specific version
docker pull etzion/spoolsync:1.0.0

# Run
docker run -d \
  -e SPOOLMAN_URL=http://<spoolman_address>/api/v1 \
  -e SPOOLEASE_URL=http://<spoolease_address> \
  -e SPOOLEASE_MODE=encrypted \
  -e SPOOLEASE_SECURITY_KEY=123456 \
  -v spoolsync-state:/state \
  etzion/spoolsync:latest
```

### With Docker Compose

Update `bridge/docker-compose.yml`:

```yaml
services:
  spoolsync:
    image: etzion/spoolsync:latest  # <-- Change this
    environment:
      SPOOLMAN_URL: http://<spoolman_address>/api/v1
      SPOOLEASE_URL: http://<spoolease_address>
      SPOOLEASE_MODE: encrypted
      SPOOLEASE_SECURITY_KEY: your-key
    volumes:
      - spoolsync-state:/state
```

Then run:

```bash
docker-compose up -d
```

## Versioning Strategy

### Development Builds
Push to `main` branch → Tagged as `latest`

```bash
git push origin main
```

### Release Versions
Use semantic versioning tags:

```bash
# Patch release (bug fixes)
git tag v1.0.1

# Minor release (new features, backward compatible)
git tag v1.1.0

# Major release (breaking changes)
git tag v2.0.0

# Push the tag
git push origin v1.0.1
```

This creates multiple tags:
- `etzion/spoolsync:1.0.1` (exact version)
- `etzion/spoolsync:1.0` (minor version)
- `etzion/spoolsync:1` (major version)
- `etzion/spoolsync:latest` (if on main branch)

## Multi-Architecture Support

The workflow automatically builds for:
- **linux/amd64**: Intel/AMD x86_64 processors
- **linux/arm64**: ARM 64-bit (Raspberry Pi 4/5, Apple Silicon)

Users can pull the appropriate architecture automatically:

```bash
# Docker automatically selects the right architecture
docker pull etzion/spoolsync:latest
```

## Troubleshooting

### Build Fails: "unauthorized"
- Check that `DOCKER_HUB_USERNAME` and `DOCKER_HUB_TOKEN` secrets are set correctly
- Verify the token has "Read, Write, Delete" permissions

### Build Fails: "denied: requested access to the resource is denied"
- Ensure you're logged in to Docker Hub
- Verify the repository name matches your Docker Hub username

### Image Too Large
The current image is optimized with:
- Python slim base image (~150MB base)
- Layer caching for dependencies
- `.dockerignore` to exclude unnecessary files

To further optimize, consider:
- Using Alpine Linux base: `FROM python:3.12-alpine`
- Multi-stage builds (not needed for this simple case)

### Slow Builds
- The workflow uses build caching to speed up subsequent builds
- First build: ~3-5 minutes
- Cached builds: ~1-2 minutes

## Security Best Practices

1. **Never commit Docker Hub credentials** to the repository
2. **Use access tokens**, not your Docker Hub password
3. **Rotate tokens periodically** (every 90 days recommended)
4. **Use read-only tokens** for pull-only scenarios
5. **Limit token scope** to specific repositories if possible

## Cleanup

To remove old images from Docker Hub:
1. Go to https://hub.docker.com/r/YOUR_USERNAME/spoolsync/tags
2. Select old tags you want to delete
3. Click "Delete"

To automate cleanup, consider:
- Docker Hub retention policies (paid plans)
- GitHub Actions workflow to delete old tags
