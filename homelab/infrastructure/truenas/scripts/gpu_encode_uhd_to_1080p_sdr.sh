#!/bin/bash
# Top Gun (1986) UHD remux -> 1080p SDR H.264 + AC3 5.1, all on GPU (NVDEC -> scale_cuda -> tonemap_opencl -> NVENC)
exec ~/topgun/bin/ffmpeg -hide_banner -y -nostdin -init_hw_device opencl=ocl -filter_hw_device ocl \
 -hwaccel cuda -hwaccel_output_format cuda -i src/00003.m2ts \
 -map 0:0 -map 0:2 \
 -vf "scale_cuda=1920:1080:format=p010le,hwdownload,format=p010le,hwupload,tonemap_opencl=tonemap=hable:desat=0:format=nv12:p=bt709:t=bt709:m=bt709:range=tv,hwdownload,format=nv12" \
 -c:v h264_nvenc -preset p6 -tune hq -rc vbr -cq 21 -b:v 0 -profile:v high \
 -c:a ac3 -ac 6 -b:a 640k -metadata:s:a:0 language=eng -disposition:a:0 default \
 -metadata title="Top Gun (1986)" -progress progress.txt out/"Top Gun (1986).mkv"
