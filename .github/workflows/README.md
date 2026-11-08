# GitHub Actions Workflows

## Docker Build and Publish

The `docker-publish.yml` workflow automatically builds and publishes the SpoolSync Docker image to Docker Hub.

### Triggers

- **Push to main branch**: Builds and tags as `latest`
- **Push git tag** (e.g., `v1.0.0`): Builds and tags with version numbers
- **Pull requests**: Builds only (does not push)

### Required Secrets

Configure these secrets in your GitHub repository settings (Settings → Secrets and variables → Actions):

1. **DOCKER_HUB_USERNAME**: Your Docker Hub username
2. **DOCKER_HUB_TOKEN**: Docker Hub access token (not password)
   - Create at: https://hub.docker.com/settings/security
   - Recommended: Create a token with "Read, Write, Delete" permissions

### Versioning

The workflow uses semantic versioning based on git tags:

```bash
# Create and push a new version
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0
```

This will create the following Docker tags:
- `username/spoolsync:1.0.0`
- `username/spoolsync:1.0`
- `username/spoolsync:1`
- `username/spoolsync:latest` (if on main branch)

### Multi-Architecture Support

The workflow builds for both:
- `linux/amd64` (Intel/AMD x86_64)
- `linux/arm64` (ARM 64-bit, e.g., Raspberry Pi 4/5, Apple Silicon)

### Usage

After the image is published, users can run:

```bash
docker pull username/spoolsync:latest
# or specific version
docker pull username/spoolsync:1.0.0
```

See the main README for complete usage instructions.
