"""Frozen file-retrieval evaluation. The output directory contains only the searchable files.

Use create_fixture(empty_folder) to materialize them, then score(metadata["queries"],
{query_id: [ranked filenames or paths]}). Keep returned metadata outside the corpus.
The query set and labels are specified independently of any search result.
"""
from array import array
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import wave


DOCUMENTS = {
    "f014.txt": "总务处借伞说明。突遇阵雨时可向一楼服务台暂借雨具，登记工号即可，次日下班之前送还。",
    "f036.txt": "总务处失物登记。有人在一楼走廊捡到一把蓝色折叠伞，失主凭伞柄上的刻字前往服务台认领。",
    "f022.txt": "冷藏货物交接记录。途中车厢温控失灵，鲜奶测得十六摄氏度，整批隔离待检，暂缓上架。",
    "f004.txt": "冷藏货物交接记录。冷链温度全程合格。部分鲜奶纸箱因卸车磕碰发生凹陷，仅替换外包装后入库。",
    "f057.txt": "会议中心无障碍指引。东门台阶旁设有斜坡，与一楼展厅平层相通，轮椅参观者可以从这里进入。",
    "f031.txt": "会议中心装卸规定。西门的货运升降平台只限搬运布展器材，访客不经此处进出。",
    "f049.txt": "温室种植日志。番茄秧叶片泛黄，检测证实土中铁元素不足。根部补充螯合铁后，新叶色泽恢复正常。",
    "f009.txt": "温室种植日志。番茄秧叶片泛黄，检测显示肥力充足；灌溉阀门长期滴水，疏通排水沟、晾干根部后恢复。",
    "f061.txt": "观测站交接。夜间云量很低，长曝光连续拍下数十道流星划过山顶的轨迹。",
    "f012.txt": "这次观星营因厚云遮挡而取消，原定参加流星雨观测的学员已领取退款。",
    "f044.txt": "窑炉修缮记录。陶土碗烧制后出现细裂纹，师傅下调前段升温速度，让坯体均匀受热。",
    "f017.txt": "Pottery workshop invoice. A visitor purchased two finished blue bowls. This receipt includes the glaze colour, paid amount and collection date.",
    "f053.txt": "Harbour operations log. Thick fog reduced visibility on the water. Passenger ferries stayed at the pier until crews could see the opposite shore again.",
    "f026.txt": "Harbour operations log. In clear weather the passenger ferry stayed at the pier for a scheduled replacement of its worn propeller.",
    "f040.txt": "羽毛球馆临时公告。周日上午木地板进行翻新，所有球场关闭，已预约的场次会自动退款。",
    "f003.txt": "归档流程。拍摄的底片先冲洗并烘干，再逐张装入无酸纸袋。馆藏编号 QV-7319 对应本批次。",
    "f058.txt": "社区图书馆搬迁公告。沿河新馆将于五月开启，自助借阅和儿童阅读区同时对外开放。",
    "f007.txt": "旧馆借阅规则。到期图书可以在手机上续借一次，已经被其他读者预约的书目除外。",
}

# Explicit colour/shape counterfactuals share the same size, background and layout.
IMAGES = {"f062.png": ("red", "circle"), "f028.png": ("blue", "circle"),
          "f047.png": ("red", "triangle"), "f011.png": ("blue", "triangle")}
AUDIO = {"f056.wav": "beeps", "f006.wav": "siren", "f033.wav": "bell"}
VIDEO = {"f024.mp4": "right", "f051.mp4": "left"}

# id, stratum, user input, relevant files, named close distractors.
# Binary file-level judgements, with ALL relevant files listed for each query.
CASES = (
    ("q01", "zh_paraphrase", "下雨忘带挡雨的东西，哪里可以先借用？", ["f014.txt"], ["f036.txt"]),
    ("q02", "zh_paraphrase", "坐轮椅去看展，从哪个入口不用爬楼梯？", ["f057.txt"], ["f031.txt"]),
    ("q03", "zh_paraphrase", "烧碗时怎么避免坯子裂开？", ["f044.txt"], ["f017.txt"]),
    ("q04", "zh_paraphrase", "手机上怎样把借书期限往后延？", ["f007.txt"], ["f058.txt"]),
    ("q05", "zh_paraphrase", "胶片显影后要怎么保存到档案里？", ["f003.txt"], []),
    ("q06", "hard_negative", "哪批牛奶因为运输中不够凉而被扣下？", ["f022.txt"], ["f004.txt"]),
    ("q07", "hard_negative", "找只坏了包装、没有失温的那批奶", ["f004.txt"], ["f022.txt"]),
    ("q08", "hard_negative", "哪次番茄黄叶是缺一种微量元素造成的？", ["f049.txt"], ["f009.txt"]),
    ("q09", "hard_negative", "哪次菜苗发黄是因为根泡在水里？", ["f009.txt"], ["f049.txt"]),
    ("q10", "hard_negative", "哪一晚真正拍到了流星，而没有取消观测？", ["f061.txt"], ["f012.txt"]),
    ("q11", "hard_negative", "因为天气没有看到流星并退钱的那次活动", ["f012.txt"], ["f061.txt"]),
    ("q12", "cross_language", "水面上雾太重，哪份记录解释了客轮为什么没能出发？", ["f053.txt"], ["f026.txt"]),
    ("q13", "cross_language", "天晴时轮渡为什么要停靠维修？", ["f026.txt"], ["f053.txt"]),
    ("q14", "cross_language", "Which sports venue refunded bookings while the floor was being resurfaced?", ["f040.txt"], []),
    ("q15", "cross_language", "Where can a visitor claim an umbrella that somebody picked up?", ["f036.txt"], ["f014.txt"]),
    ("q16", "multiple_relevant", "汇总所有记录番茄苗叶子发黄原因的种植日志", ["f009.txt", "f049.txt"], []),
    ("q17", "multiple_relevant", "Find both harbour logs about a passenger ferry remaining at the dock.", ["f026.txt", "f053.txt"], []),
    ("q18", "multiple_relevant", "找出两批冷藏鲜奶在收货时出现问题的交接记录", ["f004.txt", "f022.txt"], []),
    ("q19", "image", "白色背景中央是红色实心圆的图片", ["f062.png"], ["f028.png", "f047.png"]),
    ("q20", "image", "白底上只有一块蓝色三角形的图片", ["f011.png"], ["f028.png", "f047.png"]),
    ("q21", "image", "image of a solid blue disk on white", ["f028.png"], ["f011.png", "f062.png"]),
    ("q22", "image", "image of one solid red triangle on a white background", ["f047.png"], ["f011.png", "f062.png"]),
    ("q23", "audio", "有规律的短促哔声，每声之间安静片刻的录音", ["f056.wav"], ["f006.wav", "f033.wav"]),
    ("q24", "audio", "音调不断升高又降低、像警笛一样持续鸣响的录音", ["f006.wav"], ["f056.wav"]),
    ("q25", "audio", "a single struck bell ringing out and gradually fading audio", ["f033.wav"], ["f056.wav"]),
    ("q26", "video", "黄方块从画面左侧移到右侧的视频", ["f024.mp4"], ["f051.mp4"]),
    ("q27", "video", "video of a yellow square crossing the screen from right to left", ["f051.mp4"], ["f024.mp4"]),
    ("q28", "literal_control", "f058.txt", ["f058.txt"], []),
    ("q29", "literal_control", "QV-7319", ["f003.txt"], []),
)


def _image(path, color, shape):
    from PIL import Image, ImageDraw
    canvas = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(canvas)
    if shape == "circle":
        draw.ellipse((40, 40, 216, 216), fill=color)
    else:
        draw.polygon([(128, 30), (24, 214), (232, 214)], fill=color)
    canvas.save(path)


def _audio(path, signal):
    rate = 16000
    samples = array("h")
    for i in range(rate * 4):
        t = i / rate
        if signal == "siren":
            # Integrate the varying frequency so that the waveform is continuous.
            value = math.sin(math.tau * 680 * t - 240 / .6 * math.cos(math.tau * .6 * t))
        elif signal == "bell":
            value = math.exp(-1.4*t) * (math.sin(math.tau*523*t) + .4*math.sin(math.tau*1386*t)) / 1.4
        else:
            phase = t % .5
            envelope = max(0, min(1, phase/.007, (.13-phase)/.007))
            value = envelope * math.sin(math.tau * 920 * t)
        samples.append(round(22000 * value))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())


def _video(path, direction):
    from PIL import Image, ImageDraw
    frames = bytearray()
    for i in range(32):
        x = 8 + round(158*(i if direction == "right" else 31-i)/31)
        frame = Image.new("RGB", (224, 144), (16, 16, 16))
        ImageDraw.Draw(frame).rectangle((x, 48, x+48, 96), fill="yellow")
        frames.extend(frame.tobytes())
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FileNotFoundError("FFmpeg is required to create both labelled motion clips")
    command = [ffmpeg, "-nostdin", "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
               "-video_size", "224x144", "-framerate", "8", "-i", "pipe:0", "-an",
               "-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p", "-threads", "1",
               "-fflags", "+bitexact", "-flags:v", "+bitexact", "-map_metadata", "-1", str(path)]
    subprocess.run(command, input=frames, capture_output=True, check=True, timeout=30)


def create_fixture(folder):
    """Create the full isolated corpus. Return files, annotated queries, and SHA-256."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.iterdir()):
        raise ValueError(f"The corpus directory must be empty: {folder}")
    for name, body in DOCUMENTS.items():
        (folder / name).write_bytes(body.encode("utf-8"))
    for name, (color, shape) in IMAGES.items():
        _image(folder / name, color, shape)
    for name, signal in AUDIO.items():
        _audio(folder / name, signal)
    for name, direction in VIDEO.items():
        _video(folder / name, direction)

    files = [dict(name=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
             for p in sorted(folder.iterdir())]
    queries = [dict(id=qid, text=text, kind="", group=group, relevant=relevant.copy(),
                    hard_negatives=negatives.copy()) for qid, group, text, relevant, negatives in CASES]
    metadata = dict(version=1, files=files, queries=queries)
    canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    metadata["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return metadata


def score(queries, rankings):
    """Macro file-level hit@1/3, recall@3, MRR@10 and binary nDCG@5.

    Every query must have an entry; supply [] for an empty ranking. Only the first
    occurrence of a file counts. Truncated rankings award no credit beyond their end.
    """
    if not queries:
        raise ValueError("No queries to score")
    rows = []
    groups = defaultdict(list)
    for query in queries:
        qid = query["id"]
        relevant = set(query["relevant"])
        if not relevant:
            raise ValueError(f"No relevant files specified for {qid}")
        names = list(dict.fromkeys(PureWindowsPath(p).name for p in rankings[qid]))
        positions = [i for i, name in enumerate(names, 1) if name in relevant]
        rank = positions[0] if positions else None
        dcg = sum(1 / math.log2(i+1) for i in positions if i <= 5)
        ideal = sum(1 / math.log2(i+1) for i in range(1, min(5, len(relevant))+1))
        row = dict(id=qid, group=query["group"], first_relevant_rank=rank, top5=names[:5],
                   hit_at_1=int(rank == 1), hit_at_3=int(rank is not None and rank <= 3),
                   recall_at_3=len(relevant.intersection(names[:3])) / len(relevant),
                   mrr_at_10=1/rank if rank is not None and rank <= 10 else 0., ndcg_at_5=dcg/ideal)
        row["hard_negatives_above_first_relevant"] = [
            name for name in (names if rank is None else names[:rank-1])
            if name in query.get("hard_negatives", ())]
        rows.append(row)
        groups[query["group"]].append(row)

    def aggregate(items):
        keys = ("hit_at_1", "hit_at_3", "recall_at_3", "mrr_at_10", "ndcg_at_5")
        return {"count": len(items), **{k: sum(x[k] for x in items) / len(items) for k in keys}}

    return dict(overall=aggregate(rows), groups={k: aggregate(v) for k, v in groups.items()}, queries=rows)
