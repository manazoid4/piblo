# Adding prototype photos to the homepage

The homepage `#prototype` section has one video frame and three photo frames (`site/index.html`, class `proto-frame`).

To fill a frame:
1. Save the photo as `site/media/prototype/<name>.jpg` (under 300 KB, about 1200 px wide; the video as `.mp4` under 3 MB with a `.jpg` poster).
2. Inside that frame's `<div class="proto-frame" data-pending>`, add the image as the first child and delete `data-pending`:
   `<img src="/media/prototype/<name>.jpg" alt="What the photo shows" width="1200" height="900" loading="lazy">`
   For the video: `<video src="/media/prototype/<name>.mp4" poster="/media/prototype/<name>.jpg" muted playsinline controls preload="metadata"></video>`
3. The "coming soon" label hides by itself once `data-pending` is gone.

Shot list: (1) 20-second single take: a reminder arrives and a YES press does the job on the PC; (2) the pocket piece in its dock next to a laptop; (3) finger on the YES key with a real approval on screen; (4) paired with Piblo Core on Windows.
Unedited only: no renders and no retouching that changes what the device does.
