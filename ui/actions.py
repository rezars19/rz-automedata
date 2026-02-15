"""
RZ Automedata - Actions & Business Logic
Handles asset management (add, clear), metadata generation, CSV export, and logging.
Mixed into the main RZAutomedata class.
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import os
import pathlib
import sys

import core.database as db
from ui.theme import COLORS, PREVIEW_SIZE, compress_preview
from core.metadata_processor import get_file_type, load_preview_image, process_all_assets
from core.csv_exporter import export_csv

# Notification sound (Windows built-in)
try:
    import winsound
    HAS_SOUND = True
except ImportError:
    HAS_SOUND = False


class ActionsMixin:
    """Mixin that adds asset management, generation, CSV export, and utility methods."""

    # ─── Asset Management ────────────────────────────────────────────────────────

    def _add_assets(self, file_paths):
        """Add selected files as assets with progress popup."""
        file_paths = list(file_paths)
        total = len(file_paths)
        if total == 0:
            return

        self.empty_label.grid_forget()

        # Create progress popup
        progress_popup = ctk.CTkToplevel(self)
        progress_popup.title("Uploading Assets...")
        progress_popup.geometry("400x200")
        progress_popup.resizable(False, False)
        progress_popup.configure(fg_color=COLORS["bg_dark"])
        progress_popup.transient(self)
        progress_popup.grab_set()
        progress_popup.protocol("WM_DELETE_WINDOW", lambda: None)  # Prevent closing

        # Center on main window
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 400) // 2
        y = self.winfo_y() + (self.winfo_height() - 200) // 2
        progress_popup.geometry(f"+{x}+{y}")

        # Glow line
        ctk.CTkFrame(progress_popup, fg_color=COLORS["neon_blue"], height=3, corner_radius=0).pack(fill="x")

        # Card
        card = ctk.CTkFrame(progress_popup, fg_color=COLORS["bg_card"], corner_radius=12,
                            border_width=1, border_color=COLORS["border"])
        card.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(
            card, text="📂  Uploading Assets",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=COLORS["neon_blue"]
        ).pack(pady=(16, 8))

        progress_text = ctk.CTkLabel(
            card, text=f"0 / {total} assets",
            font=ctk.CTkFont(size=13), text_color=COLORS["text_primary"]
        )
        progress_text.pack(pady=(0, 8))

        progress_bar = ctk.CTkProgressBar(
            card, width=320, height=12, corner_radius=6,
            fg_color=COLORS["bg_input"], progress_color=COLORS["neon_blue"]
        )
        progress_bar.set(0)
        progress_bar.pack(pady=(0, 8))

        status_text = ctk.CTkLabel(
            card, text="Preparing...",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"]
        )
        status_text.pack(pady=(0, 12))

        def _process_assets():
            for i, file_path in enumerate(file_paths):
                file_type = get_file_type(file_path)
                if file_type is None:
                    self._log(f"⚠ Skipped unsupported: {os.path.basename(file_path)}")
                    self.after(0, lambda idx=i: _update_progress(idx + 1, True))
                    continue

                filename = os.path.basename(file_path)

                try:
                    raw_img = load_preview_image(file_path, file_type, size=PREVIEW_SIZE)
                    if raw_img is not None:
                        preview_img = compress_preview(raw_img)
                    else:
                        preview_img = None
                except Exception as e:
                    self._log(f"⚠ Preview error ({filename}): {type(e).__name__}: {e}")
                    preview_img = None

                asset_id = db.add_asset(file_path, file_type, "", filename)

                # UI updates must happen on main thread
                self.after(0, lambda aid=asset_id, fn=filename, ft=file_type, pi=preview_img, idx=i:
                    _add_row_and_update(aid, fn, ft, pi, idx + 1))

            # Close popup when done
            self.after(100, _finish_upload)

        def _update_progress(current, skip=False):
            try:
                progress_bar.set(current / total)
                progress_text.configure(text=f"{current} / {total} assets")
                if skip:
                    status_text.configure(text="Skipped unsupported file...")
            except Exception:
                pass

        def _add_row_and_update(asset_id, filename, file_type, preview_img, current):
            self._create_table_row(asset_id, filename, file_type, preview_img)
            self._log(f"📁 Added: {filename} ({file_type})")
            _update_progress(current)
            status_text.configure(text=f"Loading: {filename}")

        def _finish_upload():
            self._update_counter()
            try:
                progress_popup.grab_release()
                progress_popup.destroy()
            except Exception:
                pass
            self._show_toast(f"✅ {total} assets berhasil di-upload!")

        # Run in background thread
        threading.Thread(target=_process_assets, daemon=True).start()

    # ─── Generate / Stop ─────────────────────────────────────────────────────────

    def _on_generate_click(self):
        if self.is_generating:
            self._stop_generation()
        else:
            self._start_generation()

    def _start_generation(self):
        api_key = self.api_key_entry.get().strip()
        if not api_key:
            messagebox.showwarning("API Key Required", "Please enter your API key.")
            return

        provider = self.provider_var.get()
        model = self.model_var.get()
        if not model:
            messagebox.showwarning("Model Required", "Please select a model.")
            return

        custom_prompt = self.custom_prompt_entry.get("1.0", "end-1c").strip()

        # Freepik: check AI Generated + model selection
        ai_generated = False
        freepik_model_name = ""
        if self.current_platform == "freepik" and self.freepik_ai_var.get():
            ai_generated = True
            freepik_model_name = self.freepik_model_var.get()
            if not freepik_model_name:
                messagebox.showwarning("Model Required", "Select a Freepik AI model.")
                return

        # Save settings before generating
        self._save_settings()

        assets = db.get_pending_assets()
        error_assets = [a for a in db.get_all_assets() if a["status"] == "error"]
        for ea in error_assets:
            db.update_status(ea["id"], "pending")
            if ea not in assets:
                assets.append(ea)

        if not assets:
            messagebox.showinfo("No Assets", "No pending assets to process.\nAdd files or clear and re-add.")
            return

        self.is_generating = True
        self.stop_event.clear()
        self.generate_btn.configure(text="⏹  Stop", fg_color=COLORS["stop_red"], hover_color="#cc1133")

        platform_names = {"adobestock": "Adobe Stock", "shutterstock": "Shutterstock", "freepik": "Freepik"}
        self._log("─" * 50)
        self._log(f"🚀 Starting metadata generation...")
        self._log(f"   Provider: {provider} | Model: {model}")
        self._log(f"   Platform: {platform_names.get(self.current_platform, self.current_platform)}")
        if self.current_platform == "freepik" and ai_generated:
            self._log(f"   AI Generated: Yes | Freepik Model: {freepik_model_name}")
        if custom_prompt:
            self._log(f"   Custom Prompt: {custom_prompt}")
        self._log(f"   Assets to process: {len(assets)}")
        self._log("─" * 50)

        self.generation_thread = threading.Thread(
            target=self._generation_worker,
            args=(assets, provider, model, api_key, custom_prompt, self.current_platform, ai_generated),
            daemon=True
        )
        self.generation_thread.start()

    def _generation_worker(self, assets, provider, model, api_key, custom_prompt="", platform="adobestock", ai_generated=False):
        total_assets = len(assets)
        success_count = [0]
        error_count = [0]

        def on_log(msg):
            self.after(0, self._log, msg)
        def on_progress(current, total):
            self.after(0, self._update_progress, current, total)
        def on_asset_done(asset_id, result):
            if result:
                success_count[0] += 1
                self.after(0, self._update_asset_card, asset_id,
                           result["title"], result["keywords"], result.get("category", ""),
                           result.get("prompt", ""))
            else:
                error_count[0] += 1
            self.after(0, self._update_counter)
            self.after(0, self._update_csv_button_state)

        was_stopped = False
        process_all_assets(assets, provider, model, api_key,
                           self.stop_event, on_log, on_progress, on_asset_done,
                           custom_prompt=custom_prompt, platform=platform, ai_generated=ai_generated)
        was_stopped = self.stop_event.is_set()
        self.after(0, lambda: self._reset_generate_button(
            total_assets, success_count[0], error_count[0], was_stopped
        ))

    def _stop_generation(self):
        self.stop_event.set()
        self._log("⏹ Stopping generation...")

    def _reset_generate_button(self, total=0, success=0, errors=0, was_stopped=False):
        self.is_generating = False
        self.generate_btn.configure(
            text="🚀  Generate All",
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"]
        )
        # Show completion popup if there were assets processed
        if total > 0:
            self._show_generation_complete_popup(total, success, errors, was_stopped)

    def _show_generation_complete_popup(self, total, success, errors, was_stopped):
        """Show a centered popup when metadata generation is complete."""
        # ── Play notification sound ──
        if HAS_SOUND:
            try:
                if errors == 0 and not was_stopped:
                    # Success: play a pleasant "asterisk" chime
                    winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
                else:
                    # Partial/error/stopped: play "exclamation" alert
                    winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            except Exception:
                pass

        popup = ctk.CTkToplevel(self)
        popup.title("Generation Complete")
        popup.geometry("440x340")
        popup.resizable(False, False)
        popup.configure(fg_color=COLORS["bg_darkest"])
        popup.transient(self)
        popup.grab_set()

        # Center on main window
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 440) // 2
        y = self.winfo_y() + (self.winfo_height() - 340) // 2
        popup.geometry(f"+{x}+{y}")

        # Determine status
        if was_stopped:
            icon = "⏹"
            title_text = "Generation Stopped"
            title_color = COLORS["warning"]
            glow_color = COLORS["warning"]
        elif errors == 0:
            icon = "✅"
            title_text = "Generation Complete!"
            title_color = COLORS["success"]
            glow_color = COLORS["success"]
        elif success > 0:
            icon = "⚠️"
            title_text = "Generation Complete"
            title_color = COLORS["warning"]
            glow_color = COLORS["warning"]
        else:
            icon = "❌"
            title_text = "Generation Failed"
            title_color = COLORS["error"]
            glow_color = COLORS["error"]

        # Glow line
        ctk.CTkFrame(popup, fg_color=glow_color, height=3, corner_radius=0).pack(fill="x")

        # Card
        card = ctk.CTkFrame(popup, fg_color=COLORS["bg_dark"], corner_radius=14,
                            border_width=1, border_color=COLORS["border"])
        card.pack(fill="both", expand=True, padx=24, pady=20)

        # Icon
        ctk.CTkLabel(
            card, text=icon, font=ctk.CTkFont(size=44)
        ).pack(pady=(20, 5))

        # Title
        ctk.CTkLabel(
            card, text=title_text,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=title_color
        ).pack(pady=(0, 12))

        # Stats frame
        stats_frame = ctk.CTkFrame(card, fg_color=COLORS["bg_card"], corner_radius=10,
                                    border_width=1, border_color=COLORS["border"])
        stats_frame.pack(padx=30, fill="x")
        stats_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # Total
        ctk.CTkLabel(
            stats_frame, text=str(total),
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS["neon_blue"]
        ).grid(row=0, column=0, padx=8, pady=(12, 2))
        ctk.CTkLabel(
            stats_frame, text="Total",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"]
        ).grid(row=1, column=0, padx=8, pady=(0, 12))

        # Success
        ctk.CTkLabel(
            stats_frame, text=str(success),
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS["success"]
        ).grid(row=0, column=1, padx=8, pady=(12, 2))
        ctk.CTkLabel(
            stats_frame, text="Success",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"]
        ).grid(row=1, column=1, padx=8, pady=(0, 12))

        # Errors
        err_color = COLORS["error"] if errors > 0 else COLORS["text_muted"]
        ctk.CTkLabel(
            stats_frame, text=str(errors),
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=err_color
        ).grid(row=0, column=2, padx=8, pady=(12, 2))
        ctk.CTkLabel(
            stats_frame, text="Errors",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"]
        ).grid(row=1, column=2, padx=8, pady=(0, 12))

        # Subtitle message
        if was_stopped:
            sub_text = f"Stopped by user. {success} of {total} assets processed."
        elif errors == 0:
            sub_text = "All metadata generated successfully! 🎉"
        else:
            sub_text = f"{errors} asset(s) failed. Check the log for details."

        ctk.CTkLabel(
            card, text=sub_text,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
            wraplength=340, justify="center"
        ).pack(pady=(12, 4))

        # OK button
        ctk.CTkButton(
            card, text="👍  OK", command=popup.destroy,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white", font=ctk.CTkFont(size=14, weight="bold"),
            width=160, height=38, corner_radius=10
        ).pack(pady=(8, 16))

        # Auto-close after 8 seconds
        def _auto_close():
            try:
                popup.destroy()
            except Exception:
                pass

        popup.after(8000, _auto_close)

    # ─── Clear All ───────────────────────────────────────────────────────────────

    def _clear_all(self):
        if not self.asset_cards:
            return
        if self.is_generating:
            messagebox.showwarning("Busy", "Stop generation first.")
            return
        if not messagebox.askyesno("Clear All", "Remove all assets?"):
            return

        db.clear_all()

        for card in self.asset_cards.values():
            if "row_frame" in card:
                card["row_frame"].destroy()

        self.asset_cards.clear()
        self.preview_images.clear()
        self.card_row_counter = 0

        self.empty_label.grid(row=0, column=0, columnspan=len(self.col_config), pady=80)
        self._update_counter()
        self._update_csv_button_state()
        self._log("🗑 All assets cleared.")
        self.progress_label.configure(text="")

    # ─── Download CSV ────────────────────────────────────────────────────────────

    def _update_csv_button_state(self):
        """Enable/disable CSV download button based on whether any asset has metadata."""
        has_metadata = False
        for card in self.asset_cards.values():
            title = card["title"].get("1.0", "end-1c").strip()
            keywords = card["keywords"].get("1.0", "end-1c").strip()
            if title or keywords:
                has_metadata = True
                break

        if has_metadata:
            self.csv_btn.configure(
                state="normal",
                text_color=COLORS["text_primary"],
                border_color=COLORS["success"]
            )
        else:
            self.csv_btn.configure(
                state="disabled",
                text_color=COLORS["text_muted"],
                border_color=COLORS["border"]
            )

    def _download_csv(self):
        # Only export assets from the current session (cards visible in UI)
        if not self.asset_cards:
            messagebox.showinfo("No Data", "No assets to export.")
            return

        merged = []
        has_metadata = False
        for asset_id, card in self.asset_cards.items():
            # Get filename from DB for this asset
            asset = db.get_asset_by_id(asset_id)
            filename = asset["filename"] if asset else f"asset_{asset_id}"

            title = card["title"].get("1.0", "end-1c").strip()
            keywords = card["keywords"].get("1.0", "end-1c").strip()

            if title or keywords:
                has_metadata = True

            if self.current_platform == "freepik":
                prompt_text = card["prompt"].get("1.0", "end-1c").strip() if card.get("prompt") else ""
                model_text = card["model"].get().strip() if card.get("model") else ""
                merged.append({
                    "filename": filename,
                    "title": title,
                    "keywords": keywords,
                    "prompt": prompt_text,
                    "model": model_text
                })
            else:
                # Use stored category ID for CSV (not the display name)
                category = card.get("category_id", "").strip()
                merged.append({
                    "filename": filename,
                    "title": title,
                    "keywords": keywords,
                    "category": category
                })

        if not has_metadata:
            messagebox.showwarning("No Metadata", "Generate metadata first.")
            return

        desktop = str(pathlib.Path.home() / "Desktop")
        default_names = {
            "adobestock": "adobestock_metadata.csv",
            "shutterstock": "shutterstock_metadata.csv",
            "freepik": "freepik_metadata.csv"
        }
        default_name = default_names.get(self.current_platform, "metadata.csv")
        file_path = filedialog.asksaveasfilename(
            title="Save CSV", defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialdir=desktop, initialfile=default_name
        )
        if not file_path:
            return

        try:
            export_csv(merged, file_path, platform=self.current_platform)
            self._log(f"📥 CSV saved: {file_path}")
            platform_names = {"adobestock": "Adobe Stock", "shutterstock": "Shutterstock", "freepik": "Freepik"}
            platform_name = platform_names.get(self.current_platform, self.current_platform)
            messagebox.showinfo("CSV Exported", f"{platform_name} metadata exported!\n\nFile: {file_path}\nAssets: {len(merged)}")
        except Exception as e:
            self._log(f"❌ CSV error: {e}")
            messagebox.showerror("Export Error", str(e))

    # ─── Logging & Utilities ─────────────────────────────────────────────────────

    def _log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _update_progress(self, current, total):
        self.progress_label.configure(text=f"Progress: {current}/{total}")

    def _update_counter(self):
        all_assets = db.get_all_assets()
        done = sum(1 for a in all_assets if a["status"] == "done")
        self.counter_label.configure(text=f"Assets: {len(all_assets)}  |  Done: {done}")

    def _show_toast(self, message, duration=2500):
        """Show a brief toast notification at the top of the window."""
        toast = ctk.CTkFrame(
            self, fg_color=COLORS["bg_card"], corner_radius=10,
            border_width=1, border_color=COLORS["success"]
        )
        toast.place(relx=0.5, y=10, anchor="n")

        ctk.CTkLabel(
            toast, text=message,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["success"]
        ).pack(padx=20, pady=10)

        # Animate fade out
        def _remove():
            try:
                toast.destroy()
            except Exception:
                pass

        self.after(duration, _remove)
