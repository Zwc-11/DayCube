class EmbeddingAdapter:
    """Loads/downloads the embedding model only when this adapter is explicitly constructed."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as error:
            raise RuntimeError("Install semantic-search libraries: uv sync --extra vector") from error
        self.model = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.embed(texts)]
