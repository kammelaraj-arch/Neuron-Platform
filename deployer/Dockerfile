FROM python:3.12-alpine

# git over SSH for the auto-deploy fetch, plus bash, docker-cli, and
# docker-cli-compose so deploy.sh can rebuild + restart the Neuron stack.
RUN apk add --no-cache docker-cli docker-cli-compose git bash curl openssh-client

WORKDIR /app
COPY server.py .
COPY deploy.sh .
RUN chmod +x deploy.sh

EXPOSE 9090
CMD ["python", "-u", "server.py"]
