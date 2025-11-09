docker run -d \
  --name=navidrome \
  -v /mnt/music:/music \
  -v /mnt/data:/data \
  -p 4533:4533 \
  deluan/navidrome:latest
