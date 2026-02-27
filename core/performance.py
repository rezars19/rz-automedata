"""
RZ Automedata — Performance Utilities
Provides memory-efficient caching, batched UI updates, and deferred loading
to handle hundreds/thousands of files without freezing or crashing.
"""

import collections
import threading
import gc
import sys
import os
from concurrent.futures import ThreadPoolExecutor


# ─── LRU Image Cache ─────────────────────────────────────────────────────────

class LRUImageCache:
    """
    Thread-safe LRU cache for thumbnail images with maximum memory budget.
    Automatically evicts least-recently-used entries when the cache is full.
    """

    def __init__(self, max_items=200):
        """
        Args:
            max_items: Maximum number of images to keep in memory.
        """
        self._max_items = max_items
        self._cache = collections.OrderedDict()  # key -> image
        self._lock = threading.Lock()

    def get(self, key):
        """Get item from cache, returns None if not found."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def put(self, key, image):
        """Add/update item in cache, evicting old entries if needed."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = image
            else:
                if len(self._cache) >= self._max_items:
                    # Evict oldest entry
                    self._cache.popitem(last=False)
                self._cache[key] = image

    def remove(self, key):
        """Remove specific item from cache."""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        """Clear entire cache and trigger garbage collection."""
        with self._lock:
            self._cache.clear()
        gc.collect()

    @property
    def size(self):
        with self._lock:
            return len(self._cache)


# ─── Batched Widget Creator ──────────────────────────────────────────────────

class BatchedWidgetCreator:
    """
    Creates widgets in small batches spread across multiple event loop frames.
    This prevents the UI from freezing when adding hundreds of items.
    
    Usage:
        creator = BatchedWidgetCreator(root_widget, batch_size=15)
        creator.add_items(file_paths, create_fn, on_complete=callback)
    """

    def __init__(self, root, batch_size=15, delay_ms=10):
        """
        Args:
            root: The tkinter root/app object (for .after() calls).
            batch_size: Number of widgets to create per frame.
            delay_ms: Milliseconds between batches.
        """
        self._root = root
        self._batch_size = batch_size
        self._delay_ms = delay_ms
        self._pending = []
        self._create_fn = None
        self._on_progress = None
        self._on_complete = None
        self._is_running = False
        self._cancel = False
        self._total = 0
        self._processed = 0

    @property
    def is_running(self):
        return self._is_running

    def cancel(self):
        """Cancel the current batch operation."""
        self._cancel = True

    def add_items(self, items, create_fn, on_progress=None, on_complete=None):
        """
        Start batched creation.
        
        Args:
            items: Iterable of items to process.
            create_fn: Callable(item) -> result. Called for each item.
            on_progress: Callable(current, total). Progress callback.
            on_complete: Callable(). Called when all items are done.
        """
        self._pending = list(items)
        self._total = len(self._pending)
        self._processed = 0
        self._create_fn = create_fn
        self._on_progress = on_progress
        self._on_complete = on_complete
        self._is_running = True
        self._cancel = False
        self._process_next_batch()

    def _process_next_batch(self):
        """Process one batch of items, then schedule the next."""
        if self._cancel or not self._pending:
            self._is_running = False
            if self._on_complete and not self._cancel:
                self._on_complete()
            return

        batch = self._pending[:self._batch_size]
        self._pending = self._pending[self._batch_size:]

        for item in batch:
            try:
                self._create_fn(item)
            except Exception as e:
                print(f"[BatchedWidgetCreator] Error creating widget: {e}")
            self._processed += 1

        if self._on_progress:
            self._on_progress(self._processed, self._total)

        # Schedule next batch
        self._root.after(self._delay_ms, self._process_next_batch)


# ─── Thumbnail Thread Pool ───────────────────────────────────────────────────

class ThumbnailLoader:
    """
    Manages thumbnail loading with a bounded thread pool and visibility-aware
    deferred loading. Only generates thumbnails for items that are currently 
    or about to be visible on screen.
    
    Key features:
    - Fixed thread pool (no runaway thread creation)
    - LRU cache integration
    - Deduplication (won't re-generate same thumbnail)
    """

    def __init__(self, cache, max_workers=3):
        """
        Args:
            cache: LRUImageCache instance.
            max_workers: Max concurrent thumbnail generation threads.
        """
        self._cache = cache
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._in_flight = set()  # paths currently being generated
        self._lock = threading.Lock()
        self._shutdown = False

    def request(self, path, generate_fn, on_ready):
        """
        Request a thumbnail for the given path.
        
        Args:
            path: File path (cache key).
            generate_fn: Callable(path) -> PIL.Image or CTkImage.
            on_ready: Callable(path, image). Called on success (main thread
                      scheduling is the caller's responsibility).
        """
        if self._shutdown:
            return

        # Check cache first
        cached = self._cache.get(path)
        if cached is not None:
            on_ready(path, cached)
            return

        # Avoid duplicate submissions
        with self._lock:
            if path in self._in_flight:
                return
            self._in_flight.add(path)

        def _worker():
            try:
                image = generate_fn(path)
                if image is not None:
                    self._cache.put(path, image)
                    on_ready(path, image)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._in_flight.discard(path)

        try:
            self._pool.submit(_worker)
        except RuntimeError:
            # Pool already shut down
            with self._lock:
                self._in_flight.discard(path)

    def shutdown(self):
        """Shutdown the thread pool."""
        self._shutdown = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    @property
    def pending_count(self):
        with self._lock:
            return len(self._in_flight)


# ─── Memory Monitor ──────────────────────────────────────────────────────────

def get_memory_usage_mb():
    """Get current process memory usage in MB (Windows)."""
    try:
        import psutil
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def estimate_image_memory_bytes(width, height, channels=3):
    """Estimate raw PIL Image memory in bytes."""
    return width * height * channels


# ─── Chunked File Scanner ────────────────────────────────────────────────────

def scan_files_chunked(directory, extensions, chunk_size=100, on_chunk=None):
    """
    Scan directory for files in chunks to avoid blocking.
    
    Args:
        directory: Directory to scan.
        extensions: Set of extensions (e.g., {'.jpg', '.png'}).
        chunk_size: Files per chunk.
        on_chunk: Callable(chunk_list). Called with each chunk of file paths.
        
    Returns:
        Total count of files found.
    """
    count = 0
    chunk = []
    
    for entry in os.scandir(directory):
        if entry.is_file():
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in extensions:
                chunk.append(entry.path)
                count += 1
                
                if len(chunk) >= chunk_size:
                    if on_chunk:
                        on_chunk(chunk)
                    chunk = []
    
    if chunk and on_chunk:
        on_chunk(chunk)
    
    return count
