#!/usr/bin/env python3
"""
remux_to_mp4.py - convert MKV files in the Sonarr/Radarr libraries to MP4 so
Apple clients (Jellyfin iOS/iPadOS native player = AVPlayer) can Direct Play them.

Why: AVPlayer cannot open MKV. For MKV sources Jellyfin falls back to a
progressive fragmented-MP4 remux served with "Accept-Ranges: none" and no
Content-Length. AVPlayer requires byte-range support for progressive HTTP media
and fails with AVFoundationErrorDomain -11850 ("server is not correctly
configured"). A real MP4 file is served statically with range support.

Runs on the TrueNAS host (as root). TrueNAS has no ffmpeg, so ffmpeg/ffprobe run
inside a throwaway helper container created from the installed Jellyfin image.

Safety rules:
  * Video is never re-encoded. Only H.264 (8-bit) and HEVC without Dolby Vision
    are converted; anything else is left as MKV (Jellyfin will HLS-transcode it).
  * Audio Apple can't decode (DTS, TrueHD, FLAC, Opus, Vorbis, PCM) -> E-AC-3 640k.
  * Text subtitles -> sidecar .srt/.ass/.vtt, PGS -> sidecar .sup.
    Files with DVD/DVB bitmap subtitles are skipped (no lossless sidecar format).
  * Output goes to "<name>.remux-tmp.mp4", is verified (duration, stream counts,
    size), then renamed into place. The MKV is deleted only after that, so there
    is never a moment when neither file exists. Owner, mode and mtime are copied
    from the MKV (keeps Jellyfin's "date added" ordering).
  * Files whose inode changed in the last --min-age-minutes are skipped (they may
    still be importing). A lock file prevents concurrent runs.

Usage:
  remux_to_mp4.py              # dry run: print what would happen
  remux_to_mp4.py --apply      # convert, then ask Sonarr/Radarr to rescan
"""
import argparse
import datetime
import fcntl
import json
import os
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET

HOST_MEDIA = "/mnt/storage-tank/data-store/media"
CTR_MEDIA = "/media"
LIBRARY_DIRS = ["tv", "movies"]
# Sonarr/Radarr "copy" imports from Downloads are ZFS block clones, so a library MKV
# that still has a same-size twin in Downloads costs no extra space. Converting it
# would write a full new MP4 while the Downloads copy keeps the old blocks, so such
# files are skipped until the torrent (and its data) is removed.
DOWNLOADS_DIR = "/mnt/storage-tank/data-store/Downloads"
HELPER = "media-remux-helper"
JELLYFIN_CONTAINER = "ix-jellyfin-jellyfin-1"
FALLBACK_IMAGE = "jellyfin/jellyfin:10.11.11"
FFMPEG = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
FFPROBE = "/usr/lib/jellyfin-ffmpeg/ffprobe"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "remux_to_mp4.log")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".remux_to_mp4.lock")
JELLYFIN_URL = "http://127.0.0.1:30013"
JELLYFIN_KEY_FILE = "/root/.remux_jellyfin_key"  # dedicated API key "remux-to-mp4", root-only
ARR_APPS = {  # app -> (container, rescan command)
    "sonarr": ("ix-sonarr-sonarr-1", "RescanSeries"),
    "radarr": ("ix-radarr-radarr-1", "RescanMovie"),
}

APPLE_AUDIO = {"aac", "ac3", "eac3", "mp3", "alac"}
TEXT_SUBS = {"subrip": "srt", "ass": "ass", "ssa": "ass", "webvtt": "vtt", "mov_text": "srt", "text": "srt"}
TEXT_SUB_ENCODER = {"srt": "srt", "ass": "ass", "vtt": "webvtt"}
BITMAP_SUBS_OK = {"hdmv_pgs_subtitle": "sup"}
BITMAP_SUBS_SKIP = {"dvd_subtitle", "dvb_subtitle", "xsub"}


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def run(cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def to_ctr(host_path):
    return CTR_MEDIA + host_path[len(HOST_MEDIA):]


# ---------- helper container ----------

def start_helper():
    run(["docker", "rm", "-f", HELPER], check=False)
    image = run(["docker", "inspect", JELLYFIN_CONTAINER, "--format", "{{.Config.Image}}"], check=False).stdout.strip()
    image = image or FALLBACK_IMAGE
    run(["docker", "run", "-d", "--rm", "--name", HELPER, "--user", "0:0",
         "--cpus", "1", "--memory", "2g", "--network", "none",
         "-v", f"{HOST_MEDIA}:{CTR_MEDIA}", "--entrypoint", "sleep", image, "infinity"])


def stop_helper():
    run(["docker", "rm", "-f", HELPER], check=False)


def dexec(args):
    return run(["docker", "exec", HELPER] + args, check=False)


def probe(host_path):
    r = dexec([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", to_ctr(host_path)])
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


# ---------- planning ----------

def plan_file(mkv, info):
    """Return (plan dict, None) or (None, skip reason)."""
    streams = info["streams"]
    videos = [s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in streams if s["codec_type"] == "audio"]
    subs = [s for s in streams if s["codec_type"] == "subtitle"]
    if not videos:
        return None, "no video stream"
    v = videos[0]
    vcodec = v.get("codec_name")
    if vcodec == "h264":
        if "10" in (v.get("profile") or "") or "10" in (v.get("pix_fmt") or ""):
            return None, "10-bit H.264 (Apple devices cannot decode it)"
        vtag = "avc1"
    elif vcodec == "hevc":
        if any("DOVI" in (sd.get("side_data_type") or "") for sd in v.get("side_data_list", [])):
            return None, "Dolby Vision HEVC (left for Jellyfin to handle)"
        vtag = "hvc1"
    else:
        return None, f"video codec {vcodec} not handled"
    if not audios:
        return None, "no audio stream"
    for s in subs:
        if s.get("codec_name") in BITMAP_SUBS_SKIP:
            return None, f"bitmap subtitle {s.get('codec_name')} cannot be kept"
        if s.get("codec_name") not in TEXT_SUBS and s.get("codec_name") not in BITMAP_SUBS_OK:
            return None, f"unknown subtitle codec {s.get('codec_name')}"

    audio_plan = []
    for s in audios:
        if s.get("codec_name") in APPLE_AUDIO:
            audio_plan.append(("copy", s))
        else:
            audio_plan.append(("eac3", s))

    base = mkv[:-4]
    sidecars, used = [], set()
    for i, s in enumerate(subs):
        lang = (s.get("tags", {}).get("language") or "und").lower()
        disp = s.get("disposition", {})
        flags = (".forced" if disp.get("forced") else "") + (".sdh" if disp.get("hearing_impaired") else "")
        ext = TEXT_SUBS.get(s["codec_name"]) or BITMAP_SUBS_OK[s["codec_name"]]
        name = f"{base}.{lang}{flags}.{ext}"
        if name in used:
            name = f"{base}.track{i}.{lang}{flags}.{ext}"
        used.add(name)
        sidecars.append((i, s["codec_name"], ext, name))

    return {"mkv": mkv, "mp4": base + ".mp4", "tmp": base + ".remux-tmp.mp4", "vcodec": vcodec,
            "vtag": vtag, "audio": audio_plan, "sidecars": sidecars,
            "duration": float(info["format"].get("duration") or 0)}, None


# ---------- conversion ----------

def convert(p):
    """One ffmpeg pass over the source: output 1 is the MP4, then one output per
    sidecar subtitle (reading a 30 GB MKV once instead of once per track)."""
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", to_ctr(p["mkv"]),
           "-map", "0:V:0", "-map", "0:a", "-map_metadata", "0", "-map_chapters", "0",
           "-c:v", "copy", "-tag:v", p["vtag"]]
    for idx, (action, s) in enumerate(p["audio"]):
        if action == "copy":
            cmd += [f"-c:a:{idx}", "copy"]
        else:
            cmd += [f"-c:a:{idx}", "eac3", f"-b:a:{idx}", "640k"]
            if int(s.get("channels") or 2) > 6:
                cmd += [f"-ac:a:{idx}", "6"]
    cmd += ["-max_muxing_queue_size", "4096", "-movflags", "+faststart", "-f", "mp4", to_ctr(p["tmp"])]

    tmp_subs = []  # sidecar subtitles are written under temp names first
    for i, codec, ext, final in p["sidecars"]:
        tmp = final + ".remux-tmp"
        if ext == "sup":
            cmd += ["-map", f"0:s:{i}", "-c:s", "copy", "-f", "sup", to_ctr(tmp)]
        else:
            enc = TEXT_SUB_ENCODER[ext]
            # same-format streams are copied; mov_text/text are converted to SRT
            codec_arg = "copy" if codec in ("subrip", "ass", "ssa", "webvtt") else enc
            cmd += ["-map", f"0:s:{i}", "-c:s", codec_arg, "-max_muxing_queue_size", "4096", "-f", enc, to_ctr(tmp)]
        tmp_subs.append((tmp, final))

    r = dexec(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-400:]}")
    return tmp_subs


def verify(p):
    out = probe(p["tmp"])
    dur = float(out["format"].get("duration") or 0)
    tol = max(2.0, p["duration"] * 0.01)
    if abs(dur - p["duration"]) > tol:
        raise RuntimeError(f"duration mismatch src={p['duration']:.1f}s out={dur:.1f}s")
    nv = sum(1 for s in out["streams"] if s["codec_type"] == "video")
    na = sum(1 for s in out["streams"] if s["codec_type"] == "audio")
    if nv != 1 or na != len(p["audio"]):
        raise RuntimeError(f"stream count mismatch video={nv} audio={na}/{len(p['audio'])}")
    if os.path.getsize(p["tmp"]) < 0.3 * os.path.getsize(p["mkv"]):
        raise RuntimeError("output suspiciously small")


def finalize(p, tmp_subs):
    st = os.stat(p["mkv"])
    for path in [p["tmp"]] + [t for t, _ in tmp_subs]:
        os.chown(path, st.st_uid, st.st_gid)
        try:
            os.chmod(path, st.st_mode & 0o7777)
        except PermissionError:
            pass  # NFSv4-ACL datasets (aclmode=restricted) refuse chmod; the inherited ACL applies
        os.utime(path, (st.st_atime, st.st_mtime))
    os.rename(p["tmp"], p["mp4"])
    for tmp, final in tmp_subs:
        if os.path.exists(final):      # never overwrite an existing sidecar
            os.remove(tmp)
        else:
            os.rename(tmp, final)
    os.remove(p["mkv"])


def cleanup_tmp(p, tmp_subs=()):
    for path in [p["tmp"]] + [t for t, _ in tmp_subs]:
        if os.path.exists(path):
            os.remove(path)


# ---------- arr rescans ----------

def arr_rescan():
    for app, (ctr, command) in ARR_APPS.items():
        try:
            cfg = f"/mnt/.ix-apps/app_mounts/{app}/config/config.xml"
            key = ET.parse(cfg).getroot().findtext("ApiKey")
            ports = json.loads(run(["docker", "inspect", ctr, "--format", "{{json .NetworkSettings.Ports}}"]).stdout)
            port = next(b[0]["HostPort"] for b in ports.values() if b)
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v3/command", method="POST",
                                         data=json.dumps({"name": command}).encode(),
                                         headers={"X-Api-Key": key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                log(f"{app}: {command} queued (HTTP {r.status})")
        except Exception as e:  # rescan failure must not undo conversions
            log(f"{app}: rescan failed: {e}")


def jellyfin_refresh():
    """Ask Jellyfin to rescan so items point at the new .mp4 (best effort)."""
    try:
        key = open(JELLYFIN_KEY_FILE).read().strip()
        req = urllib.request.Request(f"{JELLYFIN_URL}/Library/Refresh", method="POST",
                                     headers={"Authorization": f'MediaBrowser Token="{key}"'})
        urllib.request.urlopen(req, timeout=30)
        log("jellyfin: library refresh queued")
    except Exception as e:
        log(f"jellyfin: refresh failed: {e}")


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually convert (default: dry run)")
    ap.add_argument("--min-age-minutes", type=int, default=60)
    ap.add_argument("--limit", type=int, default=0, help="stop after N conversions (0 = no limit)")
    ap.add_argument("--max-minutes", type=int, default=0,
                    help="do not start new files after this many minutes (0 = no limit)")
    args = ap.parse_args()

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another run is in progress", file=sys.stderr)
        return 1

    now = datetime.datetime.now().timestamp()
    files = []
    for d in LIBRARY_DIRS:  # TV first (small files, most watched), then movies
        found = []
        for root, _, names in os.walk(os.path.join(HOST_MEDIA, d)):
            for n in names:
                path = os.path.join(root, n)
                if n.endswith(".remux-tmp.mp4") or n.endswith(".remux-tmp"):
                    if args.apply:
                        os.remove(path)  # leftover from an interrupted run
                elif n.lower().endswith(".mkv"):
                    found.append(path)
        files += sorted(found)
    downloads_sizes = set()
    for root, _, names in os.walk(DOWNLOADS_DIR):
        for n in names:
            try:
                downloads_sizes.add(os.path.getsize(os.path.join(root, n)))
            except OSError:
                pass
    log(f"start apply={args.apply} candidates={len(files)}")

    start_helper()
    done = skipped = failed = 0
    try:
        for mkv in files:
            if args.max_minutes and datetime.datetime.now().timestamp() - now > args.max_minutes * 60:
                log("time budget reached, stopping; remaining files are picked up next run")
                break
            if os.path.getsize(mkv) in downloads_sizes:
                log(f"SKIP shares blocks with a Downloads copy (would use extra space): {mkv}")
                skipped += 1
                continue
            if now - os.stat(mkv).st_ctime < args.min_age_minutes * 60:
                log(f"SKIP recent  {mkv}")
                skipped += 1
                continue
            try:
                p, reason = plan_file(mkv, probe(mkv))
            except Exception as e:
                log(f"FAIL probe   {mkv}: {e}")
                failed += 1
                continue
            if not p:
                log(f"SKIP {reason}: {mkv}")
                skipped += 1
                continue
            if os.path.exists(p["mp4"]):
                log(f"SKIP mp4 already exists: {mkv}")
                skipped += 1
                continue
            summary = (f"{p['vcodec']} audio=[{','.join(a + ':' + s.get('codec_name', '?') for a, s in p['audio'])}] "
                       f"subs=[{','.join(e for _, _, e, _ in p['sidecars'])}]")
            if not args.apply:
                log(f"PLAN {summary} {mkv}")
                done += 1
                continue
            tmp_subs = []
            try:
                tmp_subs = convert(p)
                verify(p)
                finalize(p, tmp_subs)
                log(f"OK   {summary} {p['mp4']}")
                done += 1
                if done % 20 == 0:
                    jellyfin_refresh()
            except Exception as e:
                cleanup_tmp(p, tmp_subs)
                log(f"FAIL {mkv}: {e}")
                failed += 1
            if args.limit and done >= args.limit:
                break
    finally:
        stop_helper()
    log(f"end converted={done} skipped={skipped} failed={failed}")
    if args.apply and done:
        arr_rescan()
        jellyfin_refresh()
    return 0


if __name__ == "__main__":
    sys.exit(main())
