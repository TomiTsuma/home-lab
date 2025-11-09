docker run -d \
  -v /mnt/music:/music \
  -v /mnt/data:/config \
  -p 4040:4040 \
  airsonicadvanced/airsonic-advanced