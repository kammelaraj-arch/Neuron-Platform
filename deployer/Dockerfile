FROM python:3.12-alpine

# git + bash for the deploy.sh wrapper, docker-cli + compose for the
# Neuron stack restart. No PII, no app code — this image only owns the
# webhook listener and the shell wrapper.
RUN apk add --no-cache docker-cli docker-cli-compose git bash curl

WORKDIR /app
COPY server.py .
COPY deploy.sh .
RUN chmod +x deploy.sh

EXPOSE 9090
CMD ["python", "-u", "server.py"]
