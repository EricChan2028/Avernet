# Embedding HTTP response wire

`RealEmbeddingProvider` keeps the existing OpenAI-compatible request and JSON
response as the default contract. It also advertises a compact response format
so a capable model gateway can avoid sending thousands of floats as decimal
JSON text.

## Negotiation

For every `/embeddings` request, the client sends:

```http
Content-Type: application/json
Accept: application/x-bcsfuse-embedding-v1, application/json;q=0.9
```

The request body, authentication, retry policy, and timeout are unchanged. A
server that does not understand the compact media type simply returns
`application/json`:

```json
{"data": [{"embedding": [0.25, 0.5]}]}
```

## Compact response

A server that supports the compact response returns:

```http
Content-Type: application/x-bcsfuse-embedding-v1
```

The body is little-endian and starts with a 14-byte header:

| Field | Format | Value |
| --- | --- | --- |
| magic | 4 bytes | ASCII `BCFE` |
| version | uint8 little-endian | `1` |
| dtype | uint8 little-endian | `1` = float32 little-endian |
| dimension | uint32 little-endian | Configured embedding dimension |
| count | uint32 little-endian | Number of requested inputs |

The header is followed by exactly `count * dimension` little-endian float32
values, ordered by input index and then vector position.

For a single 4096-dimensional vector, the payload is
`14 + 4096 * 4 = 16,398` bytes. Decimal JSON size is model dependent and is
typically several times larger.

## Failure contract

The count must equal the request input count, and the declared dimension must
equal the configured model dimension. The body must have the exact declared
length, and every decoded value must be finite. An invalid compact response
raises `EmbeddingAPIError` with stable status `415`; the provider does not
attempt to reinterpret the already-declared binary body as JSON or silently
return fewer embeddings.

The compact format is an optional server optimization. A deployment remains
compatible with ordinary OpenAI-compatible embedding services, and no client
configuration is required to enable negotiation.

## Observability

The TC adapter records each successful parsed response in:

```text
bcsfuse_embedding_wire_responses_total{model="...", protocol="binary|json"}
```

This makes binary adoption and JSON fallback visible per model. A protocol error
is recorded through the existing embedding error metrics and does not increment
the successful wire-response counter.
