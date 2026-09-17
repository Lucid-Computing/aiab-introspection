# Governed inference server

This directory will contain the local llama.cpp server configuration and instructions
for obtaining a small instruct GGUF model. Do not commit model weights.

The governed inference server is outside the trusted system. It receives every
forwarded conversation. Traffic sent to it directly bypasses AIAB observation.

