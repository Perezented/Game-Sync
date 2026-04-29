#!/usr/bin/env bash
# =============================================================================
#  Game Sync — Installer / Updater
#  Supports: Steam Deck, Arch/Manjaro, Ubuntu/Debian/Mint, Fedora, openSUSE
#  Usage:  bash install.sh
# =============================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}▸ $*${RESET}"; }
success() { echo -e "${GREEN}✓ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $*${RESET}"; }
error()   { echo -e "${RED}✗ $*${RESET}" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}$*${RESET}\n"; }
ask()     { echo -e "${BOLD}${YELLOW}$*${RESET}"; }

read_prompt() {
    local prompt="$1"
    local varname="$2"
    if [[ -r /dev/tty ]]; then
        read -r -p "$prompt" "$varname" < /dev/tty
    else
        read -r -p "$prompt" "$varname"
    fi
}

# ── Constants ─────────────────────────────────────────────────────────────────
REPO="Perezented/Game-Sync"
BINARY_NAME="game-sync"
GITHUB_API="https://api.github.com/repos/${REPO}/releases/latest"
DOWNLOAD_BASE="https://github.com/${REPO}/releases/latest/download"
DESKTOP_FILE="$HOME/.local/share/applications/game-sync.desktop"
RCLONE_URL="https://downloads.rclone.org/rclone-current-linux-amd64.zip"

# ── OS Detection ──────────────────────────────────────────────────────────────
detect_os() {
    OS_NAME=""
    OS_PRETTY=""
    IS_STEAM_DECK=false

    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        source /etc/os-release
        OS_NAME="${ID:-unknown}"
        OS_PRETTY="${PRETTY_NAME:-$OS_NAME}"
        # Steam Deck: VARIANT_ID=steamdeck or ID=steamos
        if [[ "${VARIANT_ID:-}" == "steamdeck" || "${ID:-}" == "steamos" ]]; then
            IS_STEAM_DECK=true
        fi
    elif command -v uname &>/dev/null; then
        case "$(uname -s)" in
            Linux) OS_NAME="linux" ;;
            *)     OS_NAME="unknown" ;;
        esac
        OS_PRETTY="$OS_NAME"
    fi
}

# Map OS_NAME to a display category used in prompts
os_category() {
    if $IS_STEAM_DECK; then
        echo "Steam Deck (SteamOS)"
    else
        case "$OS_NAME" in
            ubuntu|debian|linuxmint|pop|elementary|zorin|kali) echo "Ubuntu/Debian/Mint" ;;
            fedora|rhel|centos|rocky|alma)                      echo "Fedora/RHEL" ;;
            arch|manjaro|endeavouros|garuda)                    echo "Arch/Manjaro" ;;
            opensuse*|sles)                                     echo "openSUSE" ;;
            *)                                                  echo "Linux (other)" ;;
        esac
    fi
}

# ── Install destination ───────────────────────────────────────────────────────
default_install_dir() {
    if $IS_STEAM_DECK; then
        echo "$HOME/Applications"
    else
        echo "$HOME/.local/bin"
    fi
}

# ── Dependency checks ─────────────────────────────────────────────────────────
require() {
    if ! command -v "$1" &>/dev/null; then
        error "Required tool '$1' not found. Install it and re-run this script."
        exit 1
    fi
}

# ── Fetch latest release tag via GitHub API ───────────────────────────────────
fetch_latest_version() {
    if command -v curl &>/dev/null; then
        curl -fsSL "$GITHUB_API" 2>/dev/null \
            | grep '"tag_name"' \
            | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/' \
            | head -1
    elif command -v wget &>/dev/null; then
        wget -qO- "$GITHUB_API" 2>/dev/null \
            | grep '"tag_name"' \
            | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/' \
            | head -1
    else
        echo "unknown"
    fi
}

# ── Download helper ───────────────────────────────────────────────────────────
download_file() {
    local url="$1" dest="$2"
    if command -v curl &>/dev/null; then
        curl -fsSL --progress-bar "$url" -o "$dest"
    elif command -v wget &>/dev/null; then
        wget -q --show-progress "$url" -O "$dest"
    else
        error "Neither curl nor wget found. Cannot download file."
        exit 1
    fi
}

# ── .desktop file creation ────────────────────────────────────────────────────
create_desktop_entry() {
    local exec_path="$1"
    mkdir -p "$(dirname "$DESKTOP_FILE")"
    cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=Game Sync
Comment=Sync game saves across machines and cloud
Exec=${exec_path}
Icon=utilities-terminal
Terminal=false
Categories=Utility;Game;
Keywords=sync;save;game;backup;
EOF
    # Refresh desktop database if available (non-fatal)
    command -v update-desktop-database &>/dev/null \
        && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    success "Desktop launcher created: $DESKTOP_FILE"
}

# ── rclone install ────────────────────────────────────────────────────────────
install_rclone_home() {
    # Install rclone to ~/.local/bin (survives SteamOS updates)
    local rclone_bin="$HOME/.local/bin/rclone"
    if [[ -x "$rclone_bin" ]]; then
        success "rclone already installed at $rclone_bin"
        return 0
    fi
    info "Downloading rclone to ~/.local/bin …"
    require curl
    require unzip
    local tmpdir
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' RETURN
    download_file "$RCLONE_URL" "$tmpdir/rclone.zip"
    unzip -q "$tmpdir/rclone.zip" -d "$tmpdir/rclone-tmp"
    mkdir -p "$HOME/.local/bin"
    cp "$tmpdir"/rclone-tmp/rclone-*/rclone "$rclone_bin"
    chmod +x "$rclone_bin"
    success "rclone installed to $rclone_bin"
    # Ensure ~/.local/bin is on PATH in .bashrc
    if ! grep -q 'HOME/.local/bin' "$HOME/.bashrc" 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
        info "Added ~/.local/bin to PATH in ~/.bashrc (run 'source ~/.bashrc' to apply now)"
    fi
}

install_rclone_system() {
    # Use the official rclone install script (requires sudo, not for Steam Deck)
    if command -v rclone &>/dev/null; then
        success "rclone is already installed: $(command -v rclone)"
        return 0
    fi
    info "Installing rclone via official script (requires sudo) …"
    require curl
    sudo -v && curl -fsSL https://rclone.org/install.sh | sudo bash
    success "rclone installed"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    clear
    echo -e "${BOLD}${CYAN}"
    echo "  ╔══════════════════════════════════════╗"
    echo "  ║        Game Sync  Installer          ║"
    echo "  ║   github.com/Perezented/Game-Sync    ║"
    echo "  ╚══════════════════════════════════════╝"
    echo -e "${RESET}"

    # ── Detect OS ─────────────────────────────────────────────────────────────
    detect_os
    local detected_category
    detected_category="$(os_category)"

    header "Step 1 — Operating System"

    if [[ "$OS_NAME" == "unknown" ]]; then
        warn "Could not detect your OS automatically."
        detected_category=""
    else
        info "Detected: ${OS_PRETTY} → ${BOLD}${detected_category}${RESET}"
    fi

    ask "Is '${detected_category:-Not detected}' correct?"
    echo "  1) Yes — continue with detected OS"
    echo "  2) No  — let me choose manually"
    read_prompt "Choice [1/2, default 1]: " os_confirm
    os_confirm="${os_confirm:-1}"

    if [[ "$os_confirm" != "1" ]]; then
        header "Select your OS"
        echo "  1) Steam Deck (SteamOS)"
        echo "  2) Ubuntu / Debian / Mint / Pop!_OS"
        echo "  3) Arch / Manjaro / EndeavourOS"
        echo "  4) Fedora / RHEL / Rocky"
        echo "  5) openSUSE"
        echo "  6) Other Linux"
        read_prompt "Choice [1-6]: " os_choice
        case "$os_choice" in
            1) IS_STEAM_DECK=true;  OS_NAME="steamos" ;;
            2) IS_STEAM_DECK=false; OS_NAME="ubuntu"  ;;
            3) IS_STEAM_DECK=false; OS_NAME="arch"    ;;
            4) IS_STEAM_DECK=false; OS_NAME="fedora"  ;;
            5) IS_STEAM_DECK=false; OS_NAME="opensuse" ;;
            *) IS_STEAM_DECK=false; OS_NAME="linux"   ;;
        esac
        detected_category="$(os_category)"
        success "Using: $detected_category"
    fi

    # ── Determine install path ─────────────────────────────────────────────────
    header "Step 2 — Install Location"
    local default_dir
    default_dir="$(default_install_dir)"
    local install_dir="$default_dir"

    ask "Where should Game Sync be installed?"
    echo "  Default: ${install_dir}"
    read_prompt "Press Enter to accept, or type a path: " custom_dir
    if [[ -n "$custom_dir" ]]; then
        install_dir="$custom_dir"
    fi
    local install_path="${install_dir}/${BINARY_NAME}"
    info "Install path: $install_path"

    # ── Check for existing install ─────────────────────────────────────────────
    header "Step 3 — Check for Existing Installation"
    local is_update=false
    local installed_ver="none"

    if [[ -x "$install_path" ]]; then
        is_update=true
        # Try to get current version (binary may not support --version, so fallback gracefully)
        installed_ver="$("$install_path" --version 2>/dev/null | head -1 || echo "unknown")"
        warn "Game Sync is already installed at: $install_path"
        info "Installed version: ${installed_ver}"
    else
        info "No existing installation found at $install_path"
    fi

    # ── Fetch latest release info ──────────────────────────────────────────────
    info "Fetching latest release from GitHub …"
    local latest_ver
    latest_ver="$(fetch_latest_version)"
    if [[ -z "$latest_ver" || "$latest_ver" == "unknown" ]]; then
        warn "Could not reach GitHub API. Will download latest anyway."
        latest_ver="latest"
    else
        success "Latest release: $latest_ver"
    fi

    if $is_update; then
        if [[ "$installed_ver" == "$latest_ver" ]]; then
            ask "You already have the latest version ($latest_ver). Re-install / force update?"
            read_prompt "Choice [y/N, default N]: " force_update
            force_update="${force_update:-N}"
            if [[ "${force_update,,}" != "y" ]]; then
                info "Skipping download — nothing to update."
                # Still offer rclone and desktop file options below
                goto_post_install "$install_path" "$detected_category" $is_update
                return 0
            fi
        else
            ask "Update from ${installed_ver} → ${latest_ver}?"
            read_prompt "Choice [Y/n, default Y]: " do_update
            do_update="${do_update:-Y}"
            if [[ "${do_update,,}" != "y" ]]; then
                info "Update cancelled."
                exit 0
            fi
        fi
    fi

    # ── Download binary ────────────────────────────────────────────────────────
    header "Step 4 — Download Game Sync"
    local download_url="${DOWNLOAD_BASE}/${BINARY_NAME}"
    info "Downloading from: $download_url"
    mkdir -p "$install_dir"

    local tmp_bin
    tmp_bin="$(mktemp)"
    download_file "$download_url" "$tmp_bin"

    # Backup existing binary if updating
    if $is_update && [[ -f "$install_path" ]]; then
        cp "$install_path" "${install_path}.bak"
        info "Backed up existing binary to ${install_path}.bak"
    fi

    mv "$tmp_bin" "$install_path"
    chmod +x "$install_path"
    success "Game Sync installed to: $install_path"

    goto_post_install "$install_path" "$detected_category" $is_update
}

# ── Post-install steps (desktop, PATH, rclone) ────────────────────────────────
goto_post_install() {
    local install_path="$1"
    local detected_category="$2"
    local is_update="$3"

    # ── PATH check ─────────────────────────────────────────────────────────────
    local install_dir
    install_dir="$(dirname "$install_path")"
    if ! echo "$PATH" | grep -q "$install_dir"; then
        warn "$install_dir is not currently in your PATH."
        ask "Add it to ~/.bashrc automatically?"
        read_prompt "Choice [Y/n, default Y]: " add_path
        add_path="${add_path:-Y}"
        if [[ "${add_path,,}" == "y" ]]; then
            if ! grep -q "$install_dir" "$HOME/.bashrc" 2>/dev/null; then
                echo "export PATH=\"${install_dir}:\$PATH\"" >> "$HOME/.bashrc"
                success "Added $install_dir to PATH in ~/.bashrc"
                info "Run 'source ~/.bashrc' or open a new terminal to apply."
            else
                info "PATH entry already present in ~/.bashrc"
            fi
        fi
    fi

    # ── Desktop launcher ───────────────────────────────────────────────────────
    header "Step 5 — Desktop Launcher"
    if [[ -f "$DESKTOP_FILE" ]]; then
        info "Desktop launcher already exists: $DESKTOP_FILE"
        ask "Re-create it (updates Exec path)?"
        read_prompt "Choice [Y/n, default Y]: " redo_desktop
        redo_desktop="${redo_desktop:-Y}"
        if [[ "${redo_desktop,,}" == "y" ]]; then
            create_desktop_entry "$install_path"
        fi
    else
        ask "Create a desktop launcher (adds Game Sync to your app menu)?"
        read_prompt "Choice [Y/n, default Y]: " make_desktop
        make_desktop="${make_desktop:-Y}"
        if [[ "${make_desktop,,}" == "y" ]]; then
            create_desktop_entry "$install_path"
        fi
    fi

    # ── SSH note ───────────────────────────────────────────────────────────────
    header "Step 6 — LAN Sync (SSH)"
    echo -e "  Game Sync uses SSH to sync saves between machines on your home network."
    echo -e "  To sync ${BOLD}to${RESET} this machine, SSH must be enabled."

    ask "Enable SSH on this machine now? (allows other machines to push saves here)"
    echo "  Note: you can always enable it later with: sudo systemctl enable --now sshd"
    read_prompt "Choice [y/N, default N]: " enable_ssh
    enable_ssh="${enable_ssh:-N}"
    if [[ "${enable_ssh,,}" == "y" ]]; then
        if command -v systemctl &>/dev/null; then
            sudo systemctl enable --now sshd \
                && success "SSH enabled and started." \
                || warn "Could not start sshd. Run manually: sudo systemctl enable --now sshd"
        else
            warn "systemctl not found. Enable SSH manually for your distribution."
        fi
        # Prompt for password if on Steam Deck (common to have no password set)
        if $IS_STEAM_DECK; then
            ask "Set a password for the 'deck' user (required for SSH login)?"
            read_prompt "Choice [y/N, default N]: " set_pass
            if [[ "${set_pass,,}" == "y" ]]; then
                passwd
            fi
        fi
    fi

    # ── rclone / Cloud Sync ────────────────────────────────────────────────────
    header "Step 7 — Cloud Sync (Google Drive / Dropbox)"
    echo -e "  Cloud sync requires ${BOLD}rclone${RESET} — a free tool."
    echo -e "  ${YELLOW}Note: rclone is only needed if you want cloud backups.${RESET}"
    echo -e "  ${YELLOW}LAN sync between two local machines does NOT require rclone.${RESET}"

    local rclone_installed=false
    if command -v rclone &>/dev/null; then
        rclone_installed=true
        success "rclone is already installed: $(command -v rclone)"
    elif [[ -x "$HOME/.local/bin/rclone" ]]; then
        rclone_installed=true
        success "rclone is already installed at ~/.local/bin/rclone"
    fi

    ask "Install rclone for cloud saves (Google Drive / Dropbox)?"
    read_prompt "Choice [y/N, default N]: " want_rclone
    want_rclone="${want_rclone:-N}"

    if [[ "${want_rclone,,}" == "y" ]]; then
        if $IS_STEAM_DECK; then
            install_rclone_home
        else
            ask "Install method:"
            echo "  1) System install via official rclone script (recommended, requires sudo)"
            echo "  2) Home directory (~/.local/bin) — safer on read-only / managed OSes"
            read_prompt "Choice [1/2, default 1]: " rclone_method
            rclone_method="${rclone_method:-1}"
            if [[ "$rclone_method" == "2" ]]; then
                install_rclone_home
            else
                install_rclone_system
            fi
        fi
    fi

    # ── Summary ────────────────────────────────────────────────────────────────
    echo ""
    echo -e "${BOLD}${GREEN}════════════════════════════════════════${RESET}"
    if $is_update; then
        echo -e "${GREEN}  Game Sync updated successfully!${RESET}"
    else
        echo -e "${GREEN}  Game Sync installed successfully!${RESET}"
    fi
    echo -e "${BOLD}${GREEN}════════════════════════════════════════${RESET}"
    echo ""
    echo -e "  ${BOLD}Binary:${RESET}  $install_path"
    [[ -f "$DESKTOP_FILE" ]] && echo -e "  ${BOLD}Launcher:${RESET} $DESKTOP_FILE"
    echo ""
    echo -e "  ${BOLD}To run:${RESET}"
    echo -e "    ${CYAN}${install_path}${RESET}"
    echo -e "  or search ${BOLD}Game Sync${RESET} in your app menu."
    echo ""
    if $IS_STEAM_DECK; then
        echo -e "  ${BOLD}Add to Steam (Game Mode):${RESET}"
        echo -e "    Games → Add a Non-Steam Game → Browse → select game-sync"
        echo ""
    fi
}

# ── Uninstall ─────────────────────────────────────────────────────────────────
uninstall() {
    clear
    echo -e "${BOLD}${RED}"
    echo "  ╔══════════════════════════════════════╗"
    echo "  ║       Game Sync  Uninstaller        ║"
    echo "  ╚══════════════════════════════════════╝"
    echo -e "${RESET}"

    detect_os
    local default_dir
    default_dir="$(default_install_dir)"

    # ── Find binary ────────────────────────────────────────────────────────────
    local candidates=(
        "${default_dir}/${BINARY_NAME}"
        "$HOME/.local/bin/${BINARY_NAME}"
        "$HOME/Applications/${BINARY_NAME}"
    )
    local found_path=""
    for c in "${candidates[@]}"; do
        if [[ -f "$c" ]]; then
            found_path="$c"
            break
        fi
    done

    if [[ -z "$found_path" ]]; then
        warn "Game Sync binary not found in any standard location."
        ask "Enter the full path to the game-sync binary (or press Enter to skip):"
        read_prompt "Path: " custom_path
        # Trim leading/trailing whitespace only — preserve internal spaces (valid in paths)
        custom_path="${custom_path#"${custom_path%%[! ]*}"}"   # ltrim
        custom_path="${custom_path%"${custom_path##*[! ]}"}"   # rtrim

        if [[ -n "$custom_path" ]]; then
            if [[ -d "$custom_path" ]]; then
                error "'$custom_path' is a directory, not a file. Aborting binary removal."
                custom_path=""
            elif [[ "$(basename "$custom_path")" != "$BINARY_NAME" ]]; then
                warn "Warning: '$(basename "$custom_path")' does not look like the Game Sync binary."
                warn "Expected filename: $BINARY_NAME"
                echo -e "  Full path: ${BOLD}${custom_path}${RESET}"
                ask "Are you sure this is the correct file?"
                read_prompt "  Type 'yes' to confirm, or press Enter to cancel: " path_confirm
                [[ "${path_confirm,,}" != "yes" ]] && { info "Binary removal cancelled."; custom_path=""; }
            elif [[ ! -f "$custom_path" ]]; then
                error "File not found: $custom_path"
                custom_path=""
            fi
        fi

        [[ -n "$custom_path" ]] && found_path="$custom_path"
    fi

    # ── Remove binary ──────────────────────────────────────────────────────────
    if [[ -n "$found_path" ]]; then
        echo ""
        info "The following file will be deleted:"
        echo -e "    ${BOLD}${found_path}${RESET}"
        [[ -f "${found_path}.bak" ]] && \
            echo -e "    ${BOLD}${found_path}.bak${RESET}  (backup)"
        ask "Confirm removal?"
        read_prompt "  Type 'yes' to delete, or press Enter to skip: " remove_bin
        if [[ "${remove_bin,,}" == "yes" ]]; then
            rm -f "$found_path"
            rm -f "${found_path}.bak"
            success "Removed: $found_path"

            # ── Optionally remove parent directory if empty ─────────────────────
            local dir
            dir="$(dirname "$found_path")"
            if [[ -d "$dir" ]] && [[ -z "$(ls -A "$dir" 2>/dev/null)" ]]; then
                echo ""
                warn "The directory containing the binary is now empty:"
                echo -e "    ${BOLD}${dir}${RESET}"
                # Warn if this is NOT one of the known safe install dirs
                local is_known_dir=false
                local known_dirs=("$HOME/.local/bin" "$HOME/Applications" "$default_dir")
                for kd in "${known_dirs[@]}"; do
                    [[ "$dir" == "$kd" ]] && { is_known_dir=true; break; }
                done
                if ! $is_known_dir; then
                    warn "This is a custom directory — deleting it will remove the entire folder."
                    warn "Make sure it does not contain other files you want to keep."
                fi
                ask "Delete this empty directory?"
                read_prompt "  Type 'yes' to delete the directory, or press Enter to keep it: " rm_dir
                if [[ "${rm_dir,,}" == "yes" ]]; then
                    rmdir "$dir" && success "Removed empty directory: $dir"
                else
                    info "Directory kept: $dir"
                fi
            fi
        else
            info "Binary removal skipped."
        fi
    else
        warn "No binary found — skipping binary removal."
    fi

    # ── Remove desktop launcher ────────────────────────────────────────────────
    if [[ -f "$DESKTOP_FILE" ]]; then
        echo ""
        info "The following file will be deleted:"
        echo -e "    ${BOLD}${DESKTOP_FILE}${RESET}"
        ask "Remove desktop launcher?"
        read_prompt "  Type 'yes' to delete, or press Enter to skip: " remove_desktop
        if [[ "${remove_desktop,,}" == "yes" ]]; then
            rm -f "$DESKTOP_FILE"
            command -v update-desktop-database &>/dev/null \
                && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
            success "Removed: $DESKTOP_FILE"
        else
            info "Desktop launcher kept."
        fi
    else
        info "No desktop launcher found — skipping."
    fi

    # ── Remove settings file ───────────────────────────────────────────────────
    local settings="$HOME/game_sync_settings.json"
    if [[ -f "$settings" ]]; then
        echo ""
        info "The following file will be deleted (contains your sync paths and preferences):"
        echo -e "    ${BOLD}${settings}${RESET}"
        ask "Remove saved settings? (default: keep)"
        read_prompt "  Type 'yes' to delete, or press Enter to keep: " remove_settings
        if [[ "${remove_settings,,}" == "yes" ]]; then
            rm -f "$settings"
            success "Removed: $settings"
        else
            info "Settings kept at $settings"
        fi
    fi

    # ── Clean up PATH entry in .bashrc (precise match only) ────────────────────
    local install_dir
    install_dir="$(dirname "${found_path:-/nonexistent}")"
    if [[ -f "$HOME/.bashrc" ]]; then
        # Match only the exact export line added by the installer, not any line
        # that merely contains the directory path (avoids collateral deletion).
        local exact_pattern="export PATH=\"${install_dir}:\$PATH\""
        if grep -qF "$exact_pattern" "$HOME/.bashrc" 2>/dev/null; then
            echo ""
            info "Found this line in ~/.bashrc that was added by the installer:"
            echo -e "    ${BOLD}${exact_pattern}${RESET}"
            ask "Remove it?"
            read_prompt "  Type 'yes' to remove, or press Enter to keep: " remove_path
            if [[ "${remove_path,,}" == "yes" ]]; then
                local tmprc
                tmprc="$(mktemp)"
                grep -vF "$exact_pattern" "$HOME/.bashrc" > "$tmprc" && mv "$tmprc" "$HOME/.bashrc"
                success "Removed PATH entry from ~/.bashrc"
            else
                info "~/.bashrc left unchanged."
            fi
        fi
    fi

    echo ""
    echo -e "${BOLD}${GREEN}════════════════════════════════════════${RESET}"
    echo -e "${GREEN}  Game Sync uninstalled.${RESET}"
    echo -e "${BOLD}${GREEN}════════════════════════════════════════${RESET}"
    echo ""
}

# ── Entry point ───────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
else
    main "$@"
fi
