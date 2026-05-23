# Psychology Channel Style

Each psychology channel folder can define its own fixed visual style:

`tools/srt-to-excel/reference_characters/psychology/TL1-T2/style.yaml`

The generator loads `style.yaml` from the active `reference_channel`. Edit only the folder for the channel you want to change. Each channel has one fixed text style profile.

Important keys:

- `style_name`: short name for workbook metadata.
- `image_style`: style sentence prepended to scene image prompts.
- `video_style`: style sentence prepended to scene video prompts.
- `thumbnail_style`: style sentence used for thumbnails.
- `palette`: color/material direction used in planning and repair.
- `negative_prompt`: style exclusions that QA and fallbacks preserve.
- `reference_lock`: how nv1.png should be preserved.

Use `style.example.yaml` as a soft anime example.
