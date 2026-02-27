"""
RZ Automedata - License & Update UI
Handles the license activation screen and the update notification popup.
These are mixed into the main RZAutomedata class via multiple inheritance.
"""

import customtkinter as ctk
from tkinter import messagebox
import threading
import webbrowser

from ui.theme import COLORS
from core.license_manager import check_license, check_for_updates, CURRENT_VERSION
from core.auto_updater import download_update, apply_update_and_restart, is_frozen


class LicenseUpdateMixin:
    """Mixin that adds license-screen and update-popup methods to the main app."""

    # ═════════════════════════════════════════════════════════════════════════════
    # LICENSE SCREEN
    # ═════════════════════════════════════════════════════════════════════════════

    def _show_license_screen(self, error_message):
        """Show license activation screen when license is invalid."""
        self.title("⚡ RZ Studio — Aktivasi Diperlukan")
        self.geometry("620x600")
        self.minsize(520, 550)
        self.resizable(False, False)

        # Main container
        main = ctk.CTkFrame(self, fg_color=COLORS["bg_darkest"])
        main.pack(fill="both", expand=True)

        # Glow line at top
        ctk.CTkFrame(main, fg_color=COLORS["neon_blue"], height=3, corner_radius=0).pack(fill="x")

        # Scrollable content
        scroll = ctk.CTkScrollableFrame(main, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=30, pady=20)

        # Card
        card = ctk.CTkFrame(
            scroll, fg_color=COLORS["bg_dark"], corner_radius=16,
            border_width=1, border_color=COLORS["border"]
        )
        card.pack(fill="x", pady=(0, 10))

        # Icon & Title
        ctk.CTkLabel(
            card, text="🔒",
            font=ctk.CTkFont(size=48)
        ).pack(pady=(25, 5))

        ctk.CTkLabel(
            card, text="Aktivasi Diperlukan",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color=COLORS["neon_blue"]
        ).pack(pady=(0, 10))

        # Error message
        ctk.CTkLabel(
            card, text=error_message,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            wraplength=420, justify="center"
        ).pack(pady=(0, 15))

        # Separator
        ctk.CTkFrame(card, fg_color=COLORS["border"], height=1).pack(fill="x", padx=30, pady=5)

        # ── Machine ID Section (Primary) ──
        ctk.CTkLabel(
            card, text="🖥️  Machine ID Anda:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["neon_blue"]
        ).pack(pady=(12, 4), padx=30, anchor="w")

        ctk.CTkLabel(
            card, text="Kirim Machine ID ini ke admin untuk aktivasi",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"]
        ).pack(padx=30, anchor="w")

        # Machine ID display with mask
        mid_outer = ctk.CTkFrame(card, fg_color=COLORS["bg_input"], corner_radius=10,
                                  border_width=1, border_color=COLORS["accent_blue"])
        mid_outer.pack(padx=30, pady=(6, 0), fill="x")

        mid_row = ctk.CTkFrame(mid_outer, fg_color="transparent")
        mid_row.pack(fill="x", padx=10, pady=8)

        machine_id_text = self.machine_id if self.machine_id else "N/A"
        machine_id_masked = "•" * len(machine_id_text)
        self._lic_mid_visible = False

        mid_label = ctk.CTkLabel(
            mid_row, text=machine_id_masked,
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color=COLORS["neon_blue"]
        )
        mid_label.pack(side="left", expand=True, fill="x")

        def toggle_mid_visibility():
            self._lic_mid_visible = not self._lic_mid_visible
            if self._lic_mid_visible:
                mid_label.configure(text=machine_id_text)
                mid_show_btn.configure(text="🙈")
            else:
                mid_label.configure(text=machine_id_masked)
                mid_show_btn.configure(text="👁")

        mid_show_btn = ctk.CTkButton(
            mid_row, text="👁", width=36, height=28,
            command=toggle_mid_visibility,
            fg_color="transparent", hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=16), corner_radius=6
        )
        mid_show_btn.pack(side="right", padx=(4, 0))

        # Machine ID buttons
        mid_btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        mid_btn_frame.pack(padx=30, pady=(8, 12), fill="x")

        def copy_mid():
            self.clipboard_clear()
            self.clipboard_append(machine_id_text)
            mid_copy_btn.configure(text="✅ Copied!")
            self.after(1500, lambda: mid_copy_btn.configure(text="📋  Copy Machine ID"))

        mid_copy_btn = ctk.CTkButton(
            mid_btn_frame, text="📋  Copy Machine ID",
            command=copy_mid,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
            height=36, corner_radius=10
        )
        mid_copy_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))

        ctk.CTkButton(
            mid_btn_frame, text="🔄  Refresh",
            command=self._retry_license_check,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            border_width=1, border_color=COLORS["border"],
            font=ctk.CTkFont(size=13, weight="bold"),
            width=120, height=36, corner_radius=10
        ).pack(side="right")

        # ── Info Footer ──
        info_card = ctk.CTkFrame(
            scroll, fg_color=COLORS["bg_card"], corner_radius=12,
            border_width=1, border_color=COLORS["border"]
        )
        info_card.pack(fill="x", pady=(8, 0))

        ctk.CTkLabel(
            info_card, text="💡 Cara Aktivasi",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(padx=20, pady=(12, 4), anchor="w")

        steps_text = (
            "1. Copy Machine ID di atas\n"
            "2. Kirim ke admin untuk aktivasi\n"
            "3. Setelah admin mengaktifkan, klik Refresh\n"
            "4. Langganan: Rp40.000/bulan (30 hari)"
        )
        ctk.CTkLabel(
            info_card, text=steps_text,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
            justify="left"
        ).pack(padx=20, pady=(0, 12), anchor="w")

    def _retry_license_check(self):
        """Re-check license and restart app if valid."""
        is_valid, result = check_license()
        if is_valid:
            messagebox.showinfo("Berhasil!", "Lisensi aktif! Aplikasi akan dimulai ulang.")
            self.destroy()
            # Import here to avoid circular import
            from app import RZAutomedata
            new_app = RZAutomedata()
            new_app.mainloop()
        else:
            messagebox.showwarning("Belum Aktif", result)

    # ═════════════════════════════════════════════════════════════════════════════
    # UPDATE POPUP
    # ═════════════════════════════════════════════════════════════════════════════

    def _check_for_updates(self):
        """Check for app updates in background thread."""
        def _do_check():
            update_info = check_for_updates()
            if update_info:
                self.after(0, lambda: self._show_update_popup(update_info))

        threading.Thread(target=_do_check, daemon=True).start()

    def _show_update_popup(self, info):
        """Show update notification popup with auto-download."""
        is_mandatory = info.get("is_mandatory", False)

        dialog = ctk.CTkToplevel(self)
        dialog.title("Update Tersedia!" if not is_mandatory else "Update Wajib!")
        dialog.geometry("500x420")
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(fg_color=COLORS["bg_darkest"])
        dialog.resizable(False, False)

        if is_mandatory:
            dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        # Glow
        ctk.CTkFrame(dialog, fg_color=COLORS["neon_blue"], height=3, corner_radius=0).pack(fill="x")

        # Content card
        card = ctk.CTkFrame(dialog, fg_color=COLORS["bg_dark"], corner_radius=14,
                            border_width=1, border_color=COLORS["border"])
        card.pack(fill="both", expand=True, padx=24, pady=20)

        # Icon
        icon = "⚠️" if is_mandatory else "🚀"
        ctk.CTkLabel(
            card, text=icon, font=ctk.CTkFont(size=40)
        ).pack(pady=(20, 5))

        # Title
        title_text = "Update Wajib!" if is_mandatory else "Update Tersedia!"
        title_color = COLORS["error"] if is_mandatory else COLORS["neon_blue"]
        ctk.CTkLabel(
            card, text=title_text,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=title_color
        ).pack(pady=(0, 8))

        # Version info
        ctk.CTkLabel(
            card, text=f"v{CURRENT_VERSION}  →  v{info['version']}",
            font=ctk.CTkFont(family="Consolas", size=15, weight="bold"),
            text_color=COLORS["success"]
        ).pack(pady=(0, 8))

        # Release notes
        if info.get("release_notes"):
            ctk.CTkLabel(
                card, text=info["release_notes"],
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_secondary"],
                wraplength=380, justify="center"
            ).pack(pady=(0, 10))

        # Progress area (hidden initially)
        progress_frame = ctk.CTkFrame(card, fg_color="transparent")

        progress_bar = ctk.CTkProgressBar(
            progress_frame, width=380, height=12,
            progress_color=COLORS["neon_blue"],
            fg_color=COLORS["bg_input"],
            corner_radius=6
        )
        progress_bar.set(0)
        progress_bar.pack(pady=(5, 3))

        progress_label = ctk.CTkLabel(
            progress_frame, text="Preparing download...",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        progress_label.pack()

        # Buttons
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.pack(pady=(10, 20))

        def _start_auto_update():
            """Download and apply update automatically."""
            download_url = info.get("download_url", "")
            if not download_url:
                messagebox.showerror("Error", "Download URL tidak tersedia.")
                return

            # Show progress, hide buttons
            btn_frame.pack_forget()
            progress_frame.pack(pady=(5, 15))

            def _on_progress(percent, dl_mb, total_mb):
                self.after(0, lambda: progress_bar.set(percent / 100))
                self.after(0, lambda: progress_label.configure(
                    text=f"Downloading... {dl_mb:.1f} / {total_mb:.1f} MB ({percent:.0f}%)"
                ))

            def _do_download():
                downloaded = download_update(download_url, on_progress=_on_progress)
                if downloaded:
                    self.after(0, lambda: progress_label.configure(
                        text="✅ Download selesai! Applying update..."
                    ))
                    self.after(0, lambda: progress_bar.set(1.0))

                    if is_frozen():
                        # Auto-replace and restart
                        self.after(500, lambda: _apply(downloaded, dialog))
                    else:
                        # Dev mode: just show success
                        self.after(500, lambda: _show_dev_done(downloaded, dialog))
                else:
                    self.after(0, lambda: _download_failed(dialog))

            threading.Thread(target=_do_download, daemon=True).start()

        def _apply(downloaded, dlg):
            """Apply update (exe mode)."""
            success = apply_update_and_restart(downloaded)
            if success:
                progress_label.configure(
                    text="🔄 Restarting...",
                    text_color=COLORS["success"]
                )
                self.after(1000, self.destroy)
            else:
                messagebox.showerror("Error", "Gagal menerapkan update. Coba download manual.")
                webbrowser.open(info["download_url"])

        def _show_dev_done(downloaded, dlg):
            """Dev mode: can't replace running script."""
            progress_label.configure(
                text=f"✅ Downloaded to: {downloaded}\nReplace file secara manual.",
                text_color=COLORS["success"]
            )
            done_frame = ctk.CTkFrame(card, fg_color="transparent")
            done_frame.pack(pady=(0, 10))
            ctk.CTkButton(
                done_frame, text="OK", command=dlg.destroy,
                fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
                text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
                width=100, height=34, corner_radius=10
            ).pack()

        def _download_failed(dlg):
            """Download failed, offer manual download."""
            progress_label.configure(
                text="❌ Download gagal. Coba download manual.",
                text_color=COLORS["error"]
            )
            fail_frame = ctk.CTkFrame(card, fg_color="transparent")
            fail_frame.pack(pady=(5, 10))
            ctk.CTkButton(
                fail_frame, text="🌐 Download Manual",
                command=lambda: webbrowser.open(info["download_url"]),
                fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
                text_color="white", font=ctk.CTkFont(size=12, weight="bold"),
                width=160, height=34, corner_radius=10
            ).pack(side="left", padx=4)
            ctk.CTkButton(
                fail_frame, text="Tutup", command=dlg.destroy,
                fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_secondary"],
                font=ctk.CTkFont(size=12),
                width=80, height=34, corner_radius=10
            ).pack(side="left", padx=4)

        # Update button
        ctk.CTkButton(
            btn_frame, text="⬇️  Update Sekarang",
            command=_start_auto_update,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
            width=180, height=38, corner_radius=10
        ).pack(side="left", padx=8)

        if not is_mandatory:
            ctk.CTkButton(
                btn_frame, text="Nanti Saja",
                command=dialog.destroy,
                fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_secondary"],
                border_width=1, border_color=COLORS["border"],
                font=ctk.CTkFont(size=13),
                width=120, height=38, corner_radius=10
            ).pack(side="left", padx=8)
        else:
            ctk.CTkButton(
                btn_frame, text="Keluar",
                command=self.destroy,
                fg_color=COLORS["error"], hover_color="#cc2244",
                text_color="white",
                font=ctk.CTkFont(size=13),
                width=120, height=38, corner_radius=10
            ).pack(side="left", padx=8)
