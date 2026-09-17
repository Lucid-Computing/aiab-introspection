# Official nginx:alpine multi-platform manifest verified via Docker Hub 2026-09-17.
FROM docker.io/library/nginx:alpine@sha256:c8497b180665e631ec92a5091125bec5b214f0e2b99409e30653a125b37557da
COPY index.html demo_prompts.json /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY entrypoint.sh /usr/local/bin/webchat-entrypoint
EXPOSE 8081
ENTRYPOINT ["/bin/sh", "/usr/local/bin/webchat-entrypoint"]
