# Top Gun (1986): GPU transcode on aeacus-dl, replaces Tdarr CPU path

## What was done
1. Scanned `/media/movies/Top Gun (1986)` into the Tdarr Movies library (`QnvftIEOk`). The source was a 59 GB UHD remux, `00003.m2ts` (HEVC 2160p HDR10, DV enhancement layer as a 2nd video stream, TrueHD Atmos + 10 AC3, 32 PGS subs).
2. Read the ffmpeg command the existing flow (`7VpKrB9tA`) built and found it unsuitable for this file:
   - It also encodes the DV enhancement layer (`-map 0:1 -c:1 libx264`), which gives a useless second video track and doubles the encode work.
   - It runs at ~3 fps on CPU (~14 h).
   - `-c:2 copy` overrides `-c:a ac3`, so TrueHD is copied, not converted.
   - There is no HDR to SDR tone-mapping.
3. Stopped the job (restarted the Tdarr app) and removed the file's record from `FileJSONDB`. The original was never modified.
4. Encoded on VM 101 (RTX 3060) with `scripts/gpu_encode_uhd_to_1080p_sdr.sh`: NVDEC -> `scale_cuda` 1080p -> `tonemap_opencl` (hable) -> `h264_nvenc` (p6, cq 21, High, 8-bit BT.709), main video only, TrueHD -> AC3 5.1 640k (eng, default). Subtitles and the other audio tracks were dropped, because the language tags were missing. Result: 10.9 GB, duration identical to source (6566.8 s), ~29 fps at the start and ~17 fps at the end, 2h31m total.
5. Copied source and result between VMs over the LAN (192.168.100.10 <-> .17), not Tailscale.
6. Verified the result (size + SHA-256 of first/last 256 MB on both ends). Deleted the movies-folder `00003.m2ts`. The untouched copy remains in `Downloads/Top Gun.1986.UHD.BLURAY/BDMV/STREAM/`.
7. Removed the temporary key `vm101-topgun-transfer` from `truenas_admin` (the 2 existing keys were preserved) and deleted `/var/tmp/topgun` on VM 101.

## Gotchas (new)
- **ffmpeg build vs driver:** BtbN `master` needs NVENC API 13.1 (driver >=610). VM 101 has driver 580 (API 13.0). Use the `n8.1` build: `ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz`. It is installed at `~/topgun/bin/` on VM 101.
- **VM 101 `/home` is a separate 73 GB LV** (root is 171 GB). It was filled to 100% for a few minutes by a first working dir placed there. Put big work files in `/var/tmp`.
- **rsync to the data-store dataset needs `-r -t --no-perms --no-owner --no-group`.** With `-a` it fails with "mkstemp ... Operation not permitted" (NFSv4 ACLs) and exits 23 after transferring 100%, deleting the temp file. Never pipe rsync through `tail` without checking for the error line.
- **Throughput:** the VM disk, not the LAN, is the bottleneck (5-47 MB/s); the pve host is also RAM-oversubscribed (VMs configured for 45 GB on a 39 GB host).
- Tdarr's file DB showed 1 record instead of 12 after the folder scan. Cause unknown. No media affected.

## Follow-ups (not done)
- Fix flow `7VpKrB9tA` (map main video only, force AC3, tone-map HDR) or add a Tdarr node on VM 101 with the NVIDIA toolkit and an NFS media mount.
- Tuna (Documents/Aether/tuna) does not remote NVENC/NVDEC, so it can't do this transcode as-is.
