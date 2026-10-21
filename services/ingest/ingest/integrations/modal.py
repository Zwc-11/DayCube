class ModalAdapter:
    def __init__(self, app_name: str = "daycube"):
        self.app_name = app_name

    def run(self, function: str, *args):
        if function not in {"transcribe", "depth"}:
            raise ValueError("Unknown DAYCUBE GPU function")
        try:
            import modal
        except ImportError as error:
            raise RuntimeError("Install the Modal extra: uv sync --extra modal") from error
        return modal.Function.from_name(self.app_name, function).remote(*args)
