"""A completed GPU download failure, distinct from lost progress tracking."""


class GpuModelDownloadFailure(ValueError):
    def __init__(self, request, message, http_status=None):
        self.request = request
        self.http_status = http_status
        super().__init__(f"{request.filename}: GPU download failed: {message}")
