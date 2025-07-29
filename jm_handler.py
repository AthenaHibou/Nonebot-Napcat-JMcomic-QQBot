from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.exception import FinishedException
from nonebot.rule import Rule
from nonebot.params import EventPlainText
import os
import asyncio

from .jm_downloader import JM_DOWNLOAD_DIR
# 全局变量（在模块顶部定义）
active_tasks = {}

from jmcomic import JmcomicClient, JmOption

async def get_album_info(album_id: str):
    option = get_option()
    client: JmcomicClient = option.build_jm_client()
    album = await asyncio.to_thread(client.album, album_id)

    title = album.title
    photo_count = album.photo_count
    chapters = album.chapter_list
    return {
        "title": title,
        "photo_count": photo_count,
        "chapter_count": len(chapters)
    }



from .jm_downloader import (
    get_option,
    download_album_by_id,
    move_album_dirs_by_photo_titles,
    safe_cleanup,
)

from .jm_tools import images_to_pdf, batch_chapter_to_pdfs, zip_pdfs

def jm_command_rule(text: str = EventPlainText()) -> bool:
    return text.lower().startswith(".jm ")

jm_handler = on_message(rule=Rule(jm_command_rule), priority=5)

def jmzip_command_rule(text: str = EventPlainText()) -> bool:
    return text.lower().startswith(".jmzip ")

jmzip_handler = on_message(rule=Rule(jmzip_command_rule), priority=5)


async def send_group_file(bot: Bot, event: MessageEvent, file_path: str):
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        await bot.send(event, "❌ 抱歉博士，白面在数据库中未检索到该文件，无法上传...")
        return

    file_size_mb = os.path.getsize(file_path) / 1024 / 1024
    if file_size_mb > 90:
        await bot.send(event, f"⚠️ 博士，由于您申请的资源体积较大（{file_size_mb:.2f} MB），白面正在向凯尔希申请带宽权限，还请博士耐心一些…")

    try:
        if not hasattr(event, "group_id"):
            await bot.send(event, "❗ 由于白面服务受到限制，请博士在群聊中使用该命令")
            return

        group_id = event.group_id
        file_name = os.path.basename(file_path)

        # 上传文件（无返回 file_id）
        await bot.call_api(
            "upload_group_file",
            group_id=group_id,
            file=file_path,
            name=file_name
        )

        # 发送提示消息
        await bot.send(event, f"[文件]{file_name} 上传成功，白面将在一分半后销毁它…")

        # 等待一段时间，确保上传完成后文件出现在群文件列表中
        await asyncio.sleep(5)

        # 获取群文件根目录
        file_list_result = await bot.call_api(
            "get_group_root_files",
            group_id=group_id
        )

        # 在列表中查找刚上传的文件
        target_file = None
        for f in file_list_result["files"]:
            if f["file_name"] == file_name:
                target_file = f
                break

        if not target_file:
            await bot.send(event, "⚠️ 抱歉博士，未查询到上传后的群文件，可能已被其它干员销毁")
            return

        # 延迟删除
        await asyncio.sleep(85)  # 前面已经 sleep 了5秒
        await bot.call_api(
            "delete_group_file",
            group_id=group_id,
            file_id=target_file["file_id"],
            busid=target_file["busid"]
        )

    except Exception as e:
        await bot.send(event, f"⚠️ 抱歉博士，连接数据库时出现了一些异常，但仍然有可能成功：{e}")



@jm_handler.handle()
async def handle_jm(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)

    # 防止重复请求
    if active_tasks.get(user_id, False):
        await bot.send(event, "⏳ 博士的上一个请求还在处理，稍微耐心一些...")
        return

    active_tasks[user_id] = True

    try:
        args = event.get_plaintext().strip().split()
        if len(args) != 2 or not args[1].isdigit():
            await jm_handler.finish("❗ 博士，请注意吟唱格式: .JM [本子ID]，例如 .JM 472537")

        album_id = args[1]

        await bot.send(event, f"📥 已接收到博士的请求，开始收集材料 {album_id}，请稍候…")


        safe_cleanup(user_id, album_id)

        option = get_option()
        album = await download_album_by_id(album_id, option)

        album_dir = move_album_dirs_by_photo_titles(album, user_id)
        if not os.path.exists(album_dir):
            await jm_handler.finish("❌ 抱歉博士，下载任务失败了：可能是主目录不存在")

        subdirs = sorted([
            d for d in os.listdir(album_dir)
            if os.path.isdir(os.path.join(album_dir, d))
        ])
        image_files = [
            f for f in os.listdir(album_dir)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ]

        if len(subdirs) == 0 and image_files:
            pdf_path = os.path.join(album_dir, f"{album_id}.pdf")
            await asyncio.to_thread(images_to_pdf, album_dir, pdf_path)
            await send_group_file(bot, event, pdf_path)

        elif len(subdirs) == 1:
            chapter_name = subdirs[0]
            chapter_dir = os.path.join(album_dir, chapter_name)
            pdf_path = os.path.join(album_dir, f"{album_id}.pdf")
            await asyncio.to_thread(images_to_pdf, chapter_dir, pdf_path)
            await send_group_file(bot, event, pdf_path)

        else:
            pdf_paths = await asyncio.to_thread(batch_chapter_to_pdfs, album_dir)
            if not pdf_paths:
                await jm_handler.finish("❌ 抱歉博士，我暂时没有发现可以打包的 章节PDF 文件")
            zip_path = os.path.join(album_dir, f"{album_id}.zip")
            await asyncio.to_thread(zip_pdfs, pdf_paths, zip_path)
            await send_group_file(bot, event, zip_path)

        await asyncio.sleep(1)
        safe_cleanup(user_id, album_id)

    except FinishedException:
        raise
    except Exception as e:
        await jm_handler.finish(f"❌ 发生错误：{e}")
    finally:
        active_tasks[user_id] = False


@jmzip_handler.handle()
async def handle_jmzip(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)

    if active_tasks.get(user_id, False):
        await bot.send(event, "⏳ 博士的上一个请求还在处理，稍微耐心一些...")
        return

    active_tasks[user_id] = True

    try:
        args = event.get_plaintext().strip().split()
        if len(args) != 2 or not args[1].isdigit():
            await jmzip_handler.finish("❗ 博士，请注意吟唱格式: .JMZIP [本子ID]，例如 .JMZIP 472537")

        album_id = args[1]
        album_dir = os.path.join(JM_DOWNLOAD_DIR, user_id, album_id)

        zip_path = os.path.join(album_dir, f"{album_id}.zip")

        if not os.path.exists(album_dir):
            await jmzip_handler.finish("❌ 博士所需要的材料还未缓存，请先使用 .JM 下载")

        if not os.path.exists(zip_path):
            pdf_paths = await asyncio.to_thread(batch_chapter_to_pdfs, album_dir)
            if not pdf_paths:
                await jmzip_handler.finish("❌ 抱歉博士，我暂时没有发现可以打包的 PDF 文件")
            await asyncio.to_thread(zip_pdfs, pdf_paths, zip_path)

        await send_group_file(bot, event, zip_path)
        await asyncio.sleep(1)
        safe_cleanup(user_id, album_id)

    except Exception as e:
        await jmzip_handler.finish(f"❌ 发生错误：{e}")
    finally:
        active_tasks[user_id] = False

import random

# def suiji_jm_command_rule(text: str = EventPlainText()) -> bool:
#     return text.lower().strip() == ".suijijm"
#
# suiji_jm_handler = on_message(rule=Rule(suiji_jm_command_rule), priority=5)



# @suiji_jm_handler.handle()
# async def handle_suiji_jm(bot: Bot, event: MessageEvent):
#     user_id = str(event.user_id)
#
#     if active_tasks.get(user_id, False):
#         await bot.send(event, "⏳ 博士的上一个请求还在处理，稍微耐心一些...")
#         return
#
#     active_tasks[user_id] = True
#
#     try:
#         await bot.send(event, "🎲 缇宝大人的万界门：正在随机召唤本子中，请稍候…")
#
#         option = get_option()
#         max_attempts = 20
#
#         for attempt in range(max_attempts):
#             album_id = str(random.randint(10000, 1599999))
#             try:
#                 album = await download_album_by_id(album_id, option)
#                 await bot.send(event, f"✅ 成功召唤到本子 ID：{album_id}！正在为您搬运材料…")
#                 break
#             except Exception:
#                 continue
#         else:
#             await suiji_jm_handler.finish("❌ 很遗憾，未能在指定次数内召唤成功，请再试一次。")
#
#         # 后续流程与 .JM 一致
#         safe_cleanup(user_id, album_id)
#         album_dir = move_album_dirs_by_photo_titles(album, user_id)
#
#         subdirs = sorted([
#             d for d in os.listdir(album_dir)
#             if os.path.isdir(os.path.join(album_dir, d))
#         ])
#         image_files = [
#             f for f in os.listdir(album_dir)
#             if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
#         ]
#
#         if len(subdirs) == 0 and image_files:
#             pdf_path = os.path.join(album_dir, f"{album_id}.pdf")
#             await asyncio.to_thread(images_to_pdf, album_dir, pdf_path)
#             await send_group_file(bot, event, pdf_path)
#
#         elif len(subdirs) == 1:
#             chapter_name = subdirs[0]
#             chapter_dir = os.path.join(album_dir, chapter_name)
#             pdf_path = os.path.join(album_dir, f"{album_id}.pdf")
#             await asyncio.to_thread(images_to_pdf, chapter_dir, pdf_path)
#             await send_group_file(bot, event, pdf_path)
#
#         else:
#             pdf_paths = await asyncio.to_thread(batch_chapter_to_pdfs, album_dir)
#             if not pdf_paths:
#                 await suiji_jm_handler.finish("❌ 抱歉博士，我暂时没有发现可以打包的 章节PDF 文件")
#             zip_path = os.path.join(album_dir, f"{album_id}.zip")
#             await asyncio.to_thread(zip_pdfs, pdf_paths, zip_path)
#             await send_group_file(bot, event, zip_path)
#
#         await asyncio.sleep(1)
#         safe_cleanup(user_id, album_id)
#
#     except Exception as e:
#         await suiji_jm_handler.finish(f"❌ 发生错误：{e}")
#     finally:
#         active_tasks[user_id] = False
