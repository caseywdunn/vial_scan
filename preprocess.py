from PIL import Image
import io


def resize_image(image_path: str, max_size: int = 1500) -> bytes:
    """
    Resize image so longest edge <= max_size.
    Returns JPEG bytes. Preserves aspect ratio.
    """
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    if max(w, h) > max_size:
        scale = max_size / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
