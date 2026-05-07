FROM alpine:3.19

# Dependencias necesarias para Hugo Extended, Git y Python Flask Panel
RUN apk add --no-cache \
    curl \
    git \
    bash \
    libc6-compat \
    libstdc++ \
    ca-certificates \
    python3 \
    py3-flask \
    procps

# Descargar Hugo Extended 0.152.0 desde GitHub
RUN curl -L -o /tmp/hugo.tar.gz \
    https://github.com/gohugoio/hugo/releases/download/v0.152.0/hugo_extended_0.152.0_linux-amd64.tar.gz \
    && tar -C /usr/local/bin -xzf /tmp/hugo.tar.gz hugo \
    && rm /tmp/hugo.tar.gz

# Directorio de trabajo dentro del contenedor
WORKDIR /site

# Ejecutaremos la app web de Flask para control y vista previa al arrancar
ENTRYPOINT ["python3", "/site/app.py"]
