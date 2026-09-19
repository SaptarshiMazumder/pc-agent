"""A transient platform failure, not evidence that the GPU lease has expired."""


class GpuKeepaliveUnavailable(RuntimeError):
    pass
