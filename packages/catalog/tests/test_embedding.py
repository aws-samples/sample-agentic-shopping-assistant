"""Cohere Embed v4 requests and responses, with Bedrock Runtime faked."""

import io
import json

import boto3
import pytest

from aria_catalog import embedding
from aria_catalog.embedding import BATCH, DIMENSION, MODEL, embed
from aria_catalog.errors import CatalogBackendError


def vector(seed: float) -> list[float]:
    return [seed] * DIMENSION


class FakeBedrock:
    """Bedrock Runtime: records each invoke_model call and answers with one vector per text.

    `shape` is "by_type" ({"float": [...]}, for `embedding_types`) or "floats" (a plain list).
    `reply` overrides the response payload.
    """

    def __init__(self, shape="by_type", reply=None):
        self.shape, self.reply, self.calls = shape, reply, []

    def invoke_model(self, **request):
        self.calls.append(request)
        texts = json.loads(request["body"])["texts"]
        vectors = [vector(float(len(self.calls)) + n / 1000) for n in range(len(texts))]
        payload = self.reply if self.reply is not None else (
            {"embeddings": {"float": vectors}, "response_type": "embeddings_by_type"}
            if self.shape == "by_type"
            else {"embeddings": vectors, "response_type": "embeddings_floats"}
        )
        return {"body": io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode())}


def test_the_request_matches_embed_v4():
    client = FakeBedrock()
    [result] = embed(["겨울 자켓"], "search_query", client=client)

    [request] = client.calls
    assert request["modelId"] == MODEL == "global.cohere.embed-v4:0"
    assert request["contentType"] == "application/json" and request["accept"] == "application/json"
    assert json.loads(request["body"]) == {
        "texts": ["겨울 자켓"],
        "input_type": "search_query",
        "embedding_types": ["float"],
        "output_dimension": 1024,
    }
    assert result == vector(1.0)


def test_texts_are_sent_96_per_call_and_vectors_keep_their_order():
    client = FakeBedrock()
    texts = [f"product {n}" for n in range(2 * BATCH + 8)]

    vectors = embed(texts, "search_document", model="us.cohere.embed-v4:0", client=client)

    assert BATCH == 96
    assert [len(json.loads(call["body"])["texts"]) for call in client.calls] == [96, 96, 8]
    assert [json.loads(call["body"])["texts"][0] for call in client.calls] == ["product 0", "product 96", "product 192"]
    assert {call["modelId"] for call in client.calls} == {"us.cohere.embed-v4:0"}
    assert {json.loads(call["body"])["input_type"] for call in client.calls} == {"search_document"}
    assert len(vectors) == len(texts)
    assert vectors[0] == vector(1.0) and vectors[95] == vector(1.095) and vectors[96] == vector(2.0)


def test_exactly_96_texts_is_one_call():
    client = FakeBedrock()
    assert len(embed(["x"] * 96, "search_document", client=client)) == 96
    assert len(client.calls) == 1


def test_no_texts_make_no_call():
    client = FakeBedrock()
    assert embed([], "search_document", client=client) == []
    assert client.calls == []


def test_a_plain_float_list_response_is_accepted():
    assert embed(["a", "b"], "search_document", client=FakeBedrock(shape="floats")) == [vector(1.0), vector(1.001)]


@pytest.mark.parametrize(
    "reply, reason",
    [
        ({"embeddings": {"float": [vector(1.0)] * 2}}, "wrong number of vectors"),
        ({"embeddings": {"float": []}}, "wrong number of vectors"),
        ({"embeddings": {"int8": [vector(1.0)]}}, "wrong number of vectors"),
        ({"embeddings": "nope"}, "wrong number of vectors"),
        ({"embeddings": {"float": [[0.5] * 1536]}}, "isn't 1024 numbers"),
        ({"embeddings": {"float": [[0.5] * 1023]}}, "isn't 1024 numbers"),
        ({"embeddings": {"float": [["0.5"] * 1024]}}, "isn't 1024 numbers"),
        ({"embeddings": {"float": [[True] * 1024]}}, "isn't 1024 numbers"),
        ({"embeddings": {"float": [None]}}, "isn't 1024 numbers"),
        ({"id": "x"}, "no embeddings"),
        (b"not json", "no embeddings"),
    ],
)
def test_a_response_of_the_wrong_shape_is_a_backend_error(reply, reason):
    with pytest.raises(CatalogBackendError, match=reason):
        embed(["a"], "search_query", client=FakeBedrock(reply=reply))


def test_an_unknown_input_type_is_refused_before_calling():
    client = FakeBedrock()
    with pytest.raises(ValueError, match="input_type"):
        embed(["a"], "classification", client=client)
    assert client.calls == []


def test_client_errors_propagate():
    class Throttled(Exception):
        pass

    class Failing:
        def invoke_model(self, **request):
            raise Throttled()

    with pytest.raises(Throttled):
        embed(["a"], "search_document", client=Failing())


@pytest.fixture
def fresh_query_client():
    embedding.query_client.cache_clear()
    yield
    embedding.query_client.cache_clear()


def test_the_query_client_has_short_timeouts_and_one_attempt(monkeypatch, fresh_query_client):
    # A hung Bedrock must fall back to the lexical search within about 4 s: 1 s to connect, 3 s to read, no retry.
    monkeypatch.setenv("AWS_REGION", "ap-northeast-2")
    created = []
    monkeypatch.setattr(boto3, "client", lambda service, **settings: created.append((service, settings)) or object())

    client = embedding.query_client()

    assert embedding.query_client() is client  # Cached for the process.
    [(service, settings)] = created
    config = settings["config"]
    assert service == "bedrock-runtime" and settings["region_name"] == "ap-northeast-2"
    assert (config.connect_timeout, config.read_timeout) == (1, 3)
    assert config.retries == {"mode": "standard", "total_max_attempts": 1}


def test_the_query_client_makes_one_attempt_and_no_retry(monkeypatch, fresh_query_client):
    # botocore reads `max_attempts` as retries; the client it builds must allow one attempt in total.
    monkeypatch.setenv("AWS_REGION", "ap-northeast-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    client = embedding.query_client()

    assert client.meta.config.retries["total_max_attempts"] == 1
    assert (client.meta.config.connect_timeout, client.meta.config.read_timeout) == (1, 3)


def test_the_query_client_needs_a_region(monkeypatch, fresh_query_client):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.setattr(boto3, "client", lambda *a, **k: pytest.fail("no client without a region"))
    with pytest.raises(CatalogBackendError, match="AWS_REGION"):
        embedding.query_client()
