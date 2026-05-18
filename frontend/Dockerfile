FROM node:20-alpine
WORKDIR /workspace
COPY package*.json ./
RUN npm install
COPY . .
RUN apk add --no-cache gettext \
    && cp /workspace/docker/entrypoint.sh /usr/local/bin/spice-sibyl-entrypoint.sh \
    && sed -i 's/\r$//g' /usr/local/bin/spice-sibyl-entrypoint.sh \
    && chmod +x /usr/local/bin/spice-sibyl-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/spice-sibyl-entrypoint.sh"]
