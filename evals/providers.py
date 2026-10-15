"""Small REST adapters. Importing this module never makes a request.

No SDK auto-retries, API-key logging, response repair, or provider fallback:
an experiment must attribute every response/failure to its actual candidate.
"""

import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .contracts import CHANGE, PREPARATION, OUTPUT_FIELDS, number

KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


def awareness_schema():
    properties = {key: {"type": "string"} for key in OUTPUT_FIELDS}
    properties["meaningful_change"]["enum"] = list(CHANGE)
    properties["preparation_category"]["enum"] = list(PREPARATION)
    return {"type": "object", "properties": properties, "required": list(OUTPUT_FIELDS),
            "additionalProperties": False}


def queue_schema(case_ids):
    return {"type": "object", "properties": {
        "order": {"type": "object", "properties": {
            cid: {"anyOf": [{"type": "integer", "minimum": 1, "maximum": len(case_ids)},
                            {"type": "string", "enum": ["Unsure"]}]} for cid in case_ids},
            "required": list(case_ids), "additionalProperties": False},
        "explanation": {"type": "string"}}, "required": ["order", "explanation"],
        "additionalProperties": False}


class ProviderError(Exception):
    def __init__(self, code, *, status="error", retryable=False):
        super().__init__(code)
        self.code, self.status, self.retryable = code, status, retryable


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("redirect_blocked")


def post_json(url, headers, payload, timeout):
    """TLS verification uses Python defaults. Keys stay in headers, never URLs."""
    request = Request(url, json.dumps(payload, allow_nan=False).encode(),
                      {"Content-Type": "application/json", **headers}, method="POST")
    try:
        # Do not accidentally route local reports/API credentials through an ambient proxy.
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ProviderError("response_too_large")
            return json.loads(raw)
    except HTTPError as exc:
        # Error bodies can echo prompts/secrets. Never log or persist them.
        code = exc.code
        exc.close()
        raise ProviderError(f"http_{code}", retryable=code in (429, 500, 502, 503, 504)) from None
    except (TimeoutError, socket.timeout):
        # Timeout may already be billed. No automatic retry of ambiguous requests.
        raise ProviderError("request_timeout", status="timeout") from None
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ProviderError("request_timeout", status="timeout") from None
        raise ProviderError("connection_error") from None
    except (ValueError, UnicodeError):
        raise ProviderError("invalid_provider_response") from None


def request_spec(model, system, user, schema, max_output_tokens):
    provider, name = model["provider"], model["model"]
    if provider == "openai":
        payload = {"model": name, "instructions": system, "input": user,
                   "store": False, "service_tier": "default", "max_output_tokens": max_output_tokens,
                   "text": {"format": {"type": "json_schema", "name": "ems_output", "strict": True, "schema": schema}}}
        if "reasoning_effort" in model:
            payload["reasoning"] = {"effort": model["reasoning_effort"]}
        return "https://api.openai.com/v1/responses", payload
    if provider == "anthropic":
        return "https://api.anthropic.com/v1/messages", {
            "model": name, "system": system, "messages": [{"role": "user", "content": user}],
            "max_tokens": max_output_tokens, "temperature": model.get("temperature", 0.2),
            "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    if provider == "gemini":
        generation = {"maxOutputTokens": max_output_tokens, "temperature": model.get("temperature", 0.2),
                      "responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": schema}}}
        if "thinking_level" in model:
            generation["thinkingConfig"] = {"thinkingLevel": model["thinking_level"]}
        return f"https://generativelanguage.googleapis.com/v1beta/models/{quote(name, safe='')}:generateContent", {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": generation}
    if provider == "ollama":
        base = model.get("base_url", "http://127.0.0.1:11434")
        parsed = urlparse(base)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Ollama endpoint must be a loopback HTTP URL without credentials/path/query")
        payload = {"model": name, "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}],
                   "stream": False, "format": schema, "keep_alive": "5m",
                   "options": {"temperature": model.get("temperature", 0.2),
                               "num_predict": max_output_tokens, "num_ctx": model.get("context_tokens", 16384)}}
        if "think" in model:
            payload["think"] = model["think"]
        return base.rstrip("/") + "/api/chat", payload
    raise ValueError(f"Unsupported provider: {provider}")


def headers_for(provider, environ):
    if provider == "ollama":
        return {}
    key = environ.get(KEY_ENV[provider])
    if not key:
        raise ValueError(f"Missing {KEY_ENV[provider]} (set it outside the notebook; never paste it into outputs)")
    if not isinstance(key, str) or any(c in key for c in ("\r", "\n")):
        raise ValueError("API key contains invalid characters")
    if provider == "openai":
        return {"Authorization": f"Bearer {key}"}
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"x-goog-api-key": key}


def parse_response(provider, raw):
    if not isinstance(raw, dict):
        raise ProviderError("invalid_provider_response")
    status, finish, content, usage, inp, out = "ok", None, "", {}, None, None
    if provider == "openai":
        usage = raw.get("usage") or {}
        inp, out = usage.get("input_tokens"), usage.get("output_tokens")
        finish = raw.get("status")
        blocks = [block for item in raw.get("output", []) for block in item.get("content", [])]
        content = "".join(b.get("text", "") for b in blocks if b.get("type") == "output_text")
        if any(b.get("type") == "refusal" for b in blocks):
            status = "refusal"
        elif finish != "completed":
            status = "error"
    elif provider == "anthropic":
        usage = raw.get("usage") or {}
        inp, out = usage.get("input_tokens"), usage.get("output_tokens")
        finish = raw.get("stop_reason")
        content = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")
        if finish == "refusal":
            status = "refusal"
        elif finish != "end_turn":
            status = "error"
    elif provider == "gemini":
        usage = raw.get("usageMetadata") or {}
        inp = usage.get("promptTokenCount")
        # Gemini bills generated thought tokens as output, not only visible JSON.
        visible, thoughts = usage.get("candidatesTokenCount"), usage.get("thoughtsTokenCount", 0)
        out = visible + thoughts if number(visible, integer=True) and number(thoughts, integer=True) else None
        candidates = raw.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        finish = candidate.get("finishReason")
        content = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
        if raw.get("promptFeedback", {}).get("blockReason") or finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
            status = "refusal"
        elif finish != "STOP":
            status = "error"
    else:
        inp, out = raw.get("prompt_eval_count"), raw.get("eval_count")
        usage = {"load_duration_ns": raw.get("load_duration"), "total_duration_ns": raw.get("total_duration")}
        finish, content = raw.get("done_reason"), raw.get("message", {}).get("content", "")
        if not raw.get("done") or finish not in ("stop", None):
            status = "error"
    output = None
    if status == "ok":
        try:
            def unique(pairs):
                result = {}
                for k, v in pairs:
                    if k in result:
                        raise ValueError("Duplicate output key")
                    result[k] = v
                return result
            output = json.loads(content, object_pairs_hook=unique,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (ValueError, TypeError):
            # Preserve malformed text as an unusable output; do not fix or hide it.
            output = content
    return {"status": status, "output": output, "raw_text": content,
            "input_tokens": inp if number(inp, integer=True) else None,
            "output_tokens": out if number(out, integer=True) else None,
            "finish_reason": finish, "actual_model": raw.get("model", raw.get("modelVersion")), "usage": usage}


def generate(model, system, user, schema, max_output_tokens, timeout, *, transport=post_json, environ=None):
    """Called only by the explicitly opted-in runner; transport is replaceable in tests."""
    url, payload = request_spec(model, system, user, schema, max_output_tokens)
    headers = headers_for(model["provider"], os.environ if environ is None else environ)
    return parse_response(model["provider"], transport(url, headers, payload, timeout))
