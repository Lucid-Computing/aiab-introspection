# Governed inference server

The governed inference server is **not trusted** and runs outside an enclave. It
receives every forwarded conversation. Requests sent directly to port 8080 bypass
AIAB observation and policy enforcement. Use synthetic conversations for the demo.

The Compose service runs the official CPU llama.cpp server image pinned to release
`b10524` and an immutable multi-architecture digest. It exposes the OpenAI-compatible
chat API on `http://localhost:8080` with model alias `aiab-demo`. The host port binds
only to loopback; containers on the same Compose network use
`http://inference:8080`. The standalone stack does not start AIAB.

## Obtain a model

Prerequisites: Docker Engine (or Docker Desktop), Docker Compose v2 with `--wait`
support, `curl`, and enough memory for a 1B–3B instruct GGUF and its context. This
configuration uses CPU inference, a 4096-token context, and one generation slot.
Choose a model with a chat template in its GGUF metadata.

From the repository root, the following downloads the 1.5B Qwen2.5 instruct model
at an explicit model-repository revision. Review the
[model card and license](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)
before downloading. The download is roughly 1 GB; no model is fetched automatically
by the server.

```sh
mkdir -p models
curl --fail --location --retry 3 \
  'https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf' \
  --output models/qwen2.5-1.5b-instruct-q4_k_m.gguf
export INFERENCE_MODEL_PATH="$PWD/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
test -s "$INFERENCE_MODEL_PATH"
```

Alternatively, export `INFERENCE_MODEL_PATH` as the absolute path to an existing
1B–3B instruct GGUF. This is the standalone Compose configuration's only variable;
set it in your shell for every command below. These instructions require an absolute
path; Compose checks that the variable is set and nonempty, but does not enforce
an absolute path. The file is mounted read-only at `/models/model.gguf`;
`create_host_path: false` makes a missing source path fail instead of creating a directory.
`models/` and `*.gguf` are ignored by Git. Never commit model weights.

## Start and verify

Run from the repository root. `--env-file /dev/null` keeps these standalone commands
independent of any project `.env`; the exported model path is still used.

```sh
docker compose --env-file /dev/null -f inference/docker-compose.yml config --quiet
docker compose --env-file /dev/null -f inference/docker-compose.yml up -d --wait --wait-timeout 300
docker compose --env-file /dev/null -f inference/docker-compose.yml ps
curl --fail --silent --show-error http://localhost:8080/health
curl --fail --silent --show-error http://localhost:8080/v1/models
curl --fail --silent --show-error http://localhost:8080/v1/chat/completions \
  --header 'Content-Type: application/json' \
  --data '{"model":"aiab-demo","messages":[{"role":"user","content":"Reply with one short greeting."}],"stream":false,"max_tokens":32}'
```

The health response should be `{"status":"ok"}` once weights load. The model list
should contain `aiab-demo`; the completion should contain generated text in
`choices[0].message.content`. Model loading can take several minutes on a laptop.
If startup times out, check that the model path is a readable GGUF and that Docker
has sufficient memory, then retry with a longer `--wait-timeout`. `/health` returns
503 while loading. Server logging and Docker log persistence are disabled so demo
conversation contents cannot be retained by container logs; this is not an
attestation or a general guarantee about the untrusted inference host.

The API and flags follow the pinned
[llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/b10524/tools/server/README.md).
The pinned [CPU image](https://github.com/ggml-org/llama.cpp/blob/b10524/.devops/cpu.Dockerfile)
includes `curl`, which the container healthcheck uses.

Stop the server without deleting your model:

```sh
docker compose --env-file /dev/null -f inference/docker-compose.yml down
```

Validating the Compose file alone does not confirm a working completion: that check
requires a locally downloaded GGUF and running the completion command above.
