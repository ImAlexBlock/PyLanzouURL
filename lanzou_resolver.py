#!/usr/bin/env python3
"""Resolve and download Lanzou/Lanzouv direct links without a browser."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


class LanzouError(RuntimeError):
    pass


@dataclass
class ResolveResult:
    share_url: str
    file_name: str | None
    file_id: str
    iframe_url: str
    middle_url: str
    direct_url: str


class LanzouResolver:
    def __init__(self, timeout: int = 20, debug: bool = False) -> None:
        self.timeout = timeout
        self.debug = debug
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
            }
        )

    def resolve(self, share_url: str) -> ResolveResult:
        share_url = self._normalize_share_url(share_url)
        share_resp = self._get(share_url)
        share_resp = self._pass_acw_challenge(share_resp)
        file_name = self._extract_title(share_resp.text)
        iframe_path = self._first_match(
            share_resp.text,
            [
                r'<iframe[^>]+src=["\']([^"\']+)["\']',
                r"<iframe[^>]+src=([^\s>]+)",
            ],
            "iframe src",
        )
        iframe_url = urljoin(share_url, unescape(iframe_path))

        iframe_resp = self._get(
            iframe_url,
            headers={"Referer": share_url},
        )
        file_id = self._extract_file_id(iframe_resp.text)
        vars_ = dict(re.findall(r"var\s+(\w+)\s*=\s*'([^']*)'", iframe_resp.text))
        ajaxdata = vars_.get("ajaxdata")
        wp_sign = vars_.get("wp_sign")
        if not ajaxdata or not wp_sign:
            raise LanzouError("missing ajaxdata/wp_sign in iframe page")

        ajax_url = urljoin(share_url, f"/ajaxm.php?file={file_id}")
        ajax_resp = self._post(
            ajax_url,
            headers=self._ajax_headers(iframe_url, origin=self._origin(share_url)),
            data={
                "action": "downprocess",
                "websignkey": ajaxdata,
                "signs": ajaxdata,
                "sign": wp_sign,
                "websign": "",
                "kd": "1",
                "ves": "1",
            },
        )
        first_json = self._json(ajax_resp.text, "first ajax")
        if str(first_json.get("zt")) != "1":
            raise LanzouError(f"first ajax failed: {first_json}")

        dom = str(first_json.get("dom") or "").replace("\\/", "/")
        token = str(first_json.get("url") or "")
        if not dom or not token:
            raise LanzouError(f"first ajax missing dom/url: {first_json}")
        middle_url = urljoin(dom.rstrip("/") + "/file/", token)
        if "toolsdown" not in middle_url:
            middle_url += "&toolsdown" if "?" in middle_url else "?toolsdown"

        middle_resp = self._get(
            middle_url,
            headers={
                "Referer": iframe_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
            allow_redirects=False,
        )
        if middle_resp.is_redirect and middle_resp.headers.get("Location"):
            direct_url = urljoin(middle_url, middle_resp.headers["Location"])
            return ResolveResult(
                share_url=share_url,
                file_name=file_name,
                file_id=file_id,
                iframe_url=iframe_url,
                middle_url=middle_url,
                direct_url=direct_url,
            )
        file_token = self._first_match(
            middle_resp.text,
            [r"'file'\s*:\s*'([^']+)'", r'"file"\s*:\s*"([^"]+)"'],
            "final file token",
        )
        final_sign = self._first_match(
            middle_resp.text,
            [r"'sign'\s*:\s*'([^']+)'", r'"sign"\s*:\s*"([^"]+)"'],
            "final sign",
        )

        final_ajax_url = urljoin(dom.rstrip("/") + "/file/", "ajax.php")
        final_json = None
        final_errs: list[str] = []
        for el in self._candidate_el_values(middle_resp.text):
            final_resp = self._post(
                final_ajax_url,
                headers=self._ajax_headers(middle_url, origin=self._origin(dom)),
                data={"file": file_token, "el": el, "sign": final_sign},
            )
            parsed = self._json(final_resp.text, f"final ajax el={el}")
            self._log(f"final ajax el={el}: {parsed}")
            if (
                str(parsed.get("zt")) == "1"
                and parsed.get("url")
                and parsed.get("url") != "?SignError"
            ):
                final_json = parsed
                break
            final_errs.append(f"el={el}: {parsed}")
            time.sleep(0.15)
        if final_json is None:
            raise LanzouError("final ajax failed: " + " | ".join(final_errs))

        direct_url = str(final_json["url"]).replace("\\/", "/")
        if direct_url.startswith("//"):
            direct_url = "https:" + direct_url
        elif direct_url.startswith("?"):
            direct_url = urljoin(dom.rstrip("/") + "/file/", direct_url)
        elif not direct_url.startswith(("http://", "https://")):
            direct_url = urljoin(dom.rstrip("/") + "/file/", direct_url)

        return ResolveResult(
            share_url=share_url,
            file_name=file_name,
            file_id=file_id,
            iframe_url=iframe_url,
            middle_url=middle_url,
            direct_url=direct_url,
        )

    def download(
        self, direct_url: str, output: str | None = None, referer: str | None = None
    ) -> Path:
        headers = {
            "Accept": "application/octet-stream,application/zip,*/*",
        }
        if referer:
            headers["Referer"] = referer
        with self.session.get(
            direct_url, headers=headers, stream=True, timeout=self.timeout
        ) as resp:
            resp.raise_for_status()
            out = Path(output or self._filename_from_response(resp, direct_url))
            if out.is_dir():
                out = out / self._filename_from_response(resp, direct_url)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(out.suffix + ".part")
            with tmp.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        fh.write(chunk)
            os.replace(tmp, out)
            return out

    def _get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        self._log(f"GET {url}")
        resp = self.session.get(
            url,
            headers=headers,
            timeout=self.timeout,
            allow_redirects=allow_redirects,
        )
        resp.raise_for_status()
        self._fix_encoding(resp)
        return resp

    def _post(
        self, url: str, headers: dict[str, str], data: dict[str, str]
    ) -> requests.Response:
        self._log(f"POST {url} data={data}")
        resp = self.session.post(url, headers=headers, data=data, timeout=self.timeout)
        resp.raise_for_status()
        self._fix_encoding(resp)
        return resp

    def _fix_encoding(self, resp: requests.Response) -> None:
        if "text" in resp.headers.get("Content-Type", "") or resp.content.startswith(
            b"<!"
        ):
            resp.encoding = "utf-8"

    def _pass_acw_challenge(self, resp: requests.Response) -> requests.Response:
        arg1 = self._extract_acw_arg1(resp.text)
        if not arg1:
            return resp
        parsed = urlparse(resp.url)
        self.session.cookies.set(
            "acw_sc__v2",
            self._make_acw_cookie(arg1),
            domain=parsed.hostname,
            path="/",
        )
        self._log("set acw_sc__v2 anti-bot cookie")
        return self._get(resp.url)

    def _extract_acw_arg1(self, html: str) -> str | None:
        match = re.search(r"\barg1\s*=\s*['\"]([0-9A-Fa-f]{40})['\"]", html)
        if match:
            return match.group(1)
        return None

    def _make_acw_cookie(self, arg1: str) -> str:
        order = [
            0xF,
            0x23,
            0x1D,
            0x18,
            0x21,
            0x10,
            0x1,
            0x26,
            0xA,
            0x9,
            0x13,
            0x1F,
            0x28,
            0x1B,
            0x16,
            0x17,
            0x19,
            0xD,
            0x6,
            0xB,
            0x27,
            0x12,
            0x14,
            0x8,
            0xE,
            0x15,
            0x20,
            0x1A,
            0x2,
            0x1E,
            0x7,
            0x4,
            0x11,
            0x5,
            0x3,
            0x1C,
            0x22,
            0x25,
            0xC,
            0x24,
        ]
        key = "3000176000856006061501533003690027800375"
        chars = [""] * len(order)
        for index, char in enumerate(arg1):
            for out_index, value in enumerate(order):
                if value == index + 1:
                    chars[out_index] = char
                    break
        mixed = "".join(chars)
        result = []
        for index in range(0, min(len(mixed), len(key)), 2):
            value = int(mixed[index : index + 2], 16) ^ int(key[index : index + 2], 16)
            result.append(f"{value:02x}")
        return "".join(result)

    def _ajax_headers(self, referer: str, origin: str) -> dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": origin,
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        }

    def _candidate_el_values(self, html: str) -> Iterable[str]:
        values = re.findall(r"down_r\((\d+)\)", html)
        preferred = ["2", "1", "3"]
        seen: set[str] = set()
        for val in preferred + values:
            if val not in seen:
                seen.add(val)
                yield val

    def _extract_file_id(self, html: str) -> str:
        ids = re.findall(r"ajaxm\.php\?file=(\d+)", html)
        ids = [item for item in ids if item != "1"] or ids
        if not ids:
            raise LanzouError("missing ajaxm file id")
        return ids[-1]

    def _extract_title(self, html: str) -> str | None:
        match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
        if not match:
            return None
        title = unescape(re.sub(r"\s+", " ", match.group(1))).strip()
        title = re.sub(r"\s*-\s*蓝奏云\s*$", "", title)
        return title or None

    def _first_match(self, text: str, patterns: Iterable[str], label: str) -> str:
        for pattern in patterns:
            match = re.search(pattern, text, re.S | re.I)
            if match:
                return unescape(match.group(1))
        raise LanzouError(f"missing {label}")

    def _json(self, text: str, label: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            preview = text[:200].replace("\n", " ")
            raise LanzouError(f"invalid json from {label}: {preview}") from exc

    def _normalize_share_url(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def _origin(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _filename_from_response(self, resp: requests.Response, url: str) -> str:
        disposition = resp.headers.get("Content-Disposition", "")
        match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
        if match:
            return self._safe_filename(unquote(match.group(1)))
        match = re.search(r'filename="?([^";]+)"?', disposition, re.I)
        if match:
            return self._safe_filename(unquote(match.group(1)))
        qs_name = parse_qs(urlparse(url).query).get("fileName", [None])[0]
        if qs_name:
            return self._safe_filename(unquote(qs_name))
        path_name = Path(urlparse(url).path).name or "download.bin"
        return self._safe_filename(unquote(path_name))

    def _safe_filename(self, name: str) -> str:
        name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip().strip(".")
        return name or "download.bin"

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[debug] {message}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve Lanzou/Lanzouv final download links."
    )
    parser.add_argument(
        "url", help="share URL"
    )
    parser.add_argument(
        "-d", "--download", action="store_true", help="download the resolved file"
    )
    parser.add_argument(
        "-o", "--output", help="output file or directory when --download is used"
    )
    parser.add_argument(
        "--debug", action="store_true", help="print protocol debug logs"
    )
    parser.add_argument(
        "--timeout", type=int, default=20, help="request timeout in seconds"
    )
    args = parser.parse_args()

    resolver = LanzouResolver(timeout=args.timeout, debug=args.debug)
    result = resolver.resolve(args.url)
    print(result.direct_url)
    if args.debug:
        print(f"file_id={result.file_id}", file=sys.stderr)
        print(f"file_name={result.file_name or ''}", file=sys.stderr)
        print(f"iframe_url={result.iframe_url}", file=sys.stderr)
        print(f"middle_url={result.middle_url}", file=sys.stderr)
    if args.download:
        path = resolver.download(
            result.direct_url, args.output, referer=result.middle_url
        )
        print(f"downloaded: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
