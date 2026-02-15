"""Create proper multi-size ICO from logo.png for Windows shortcut compatibility."""
from PIL import Image
import struct
import io
import os

def create_ico_from_png(png_path, ico_path):
    """Create a proper Windows ICO with multiple sizes as embedded PNGs."""
    img = Image.open(png_path).convert("RGBA")
    sizes = [256, 128, 64, 48, 32, 24, 16]
    
    # Prepare all size variants
    images_data = []
    for s in sizes:
        resized = img.resize((s, s), Image.LANCZOS)
        # Save each as PNG in memory
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        png_data = buf.getvalue()
        images_data.append((s, png_data))
    
    # Build ICO file manually
    # ICO Header: 6 bytes
    num_images = len(images_data)
    ico_header = struct.pack('<HHH', 0, 1, num_images)  # reserved, type=ICO, count
    
    # Calculate offsets
    # Each directory entry is 16 bytes
    dir_size = 16 * num_images
    data_offset = 6 + dir_size  # header + directory entries
    
    directory_entries = []
    image_blobs = []
    current_offset = data_offset
    
    for size, png_data in images_data:
        w = 0 if size == 256 else size  # 0 means 256
        h = 0 if size == 256 else size
        entry = struct.pack('<BBBBHHII',
            w,              # width (0 = 256)
            h,              # height (0 = 256)  
            0,              # color palette
            0,              # reserved
            1,              # color planes
            32,             # bits per pixel
            len(png_data),  # size of image data
            current_offset  # offset to image data
        )
        directory_entries.append(entry)
        image_blobs.append(png_data)
        current_offset += len(png_data)
    
    # Write the ICO file
    with open(ico_path, 'wb') as f:
        f.write(ico_header)
        for entry in directory_entries:
            f.write(entry)
        for blob in image_blobs:
            f.write(blob)
    
    file_size = os.path.getsize(ico_path)
    print(f"Created {ico_path}: {file_size:,} bytes")
    print(f"Contains {num_images} sizes: {[s for s, _ in images_data]}")
    
    # Verify
    ico = Image.open(ico_path)
    print(f"Verification - sizes in ICO: {ico.info.get('sizes')}")

create_ico_from_png("logo.png", "icon.ico")
