# Self-Development Channel Style

Topic: **success** (phat trien ban than / self-development / personal growth).

Each self-development channel folder can define its own fixed visual style:

`tools/srt-to-excel/reference_characters/success/MT1-T2/style.yaml`

The generator loads `style.yaml` from the active `reference_channel`. Edit only the folder for the channel you want to change. Each channel has one fixed text style profile.

Active channels: `MT1-T1` .. `MT1-T10`, one per audience language
(Spanish, Vietnamese, English, French, German, Portuguese, Japanese, Korean, Italian, Turkish).

Important keys:

- `style_name`: short name for workbook metadata.
- `image_style`: style sentence prepended to scene image prompts.
- `video_style`: style sentence prepended to scene video prompts.
- `thumbnail_style`: style sentence used for thumbnails.
- `palette`: color/material direction used in planning and repair.
- `negative_prompt`: style exclusions that QA and fallbacks preserve.
- `reference_lock`: how nv1.png should be preserved.

Use `style.example.yaml` as a soft reference example.
