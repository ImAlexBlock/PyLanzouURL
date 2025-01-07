import re
import json
import requests
from urllib.parse import urljoin
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UserAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/72.0.3626.121 Safari/537.36'

def mlooc_curl_get(url, UserAgent=UserAgent):
    headers = {
        'User-Agent': UserAgent,
        'X-FORWARDED-FOR': rand_ip(),
        'CLIENT-IP': rand_ip()
    }
    response = requests.get(url, headers=headers, verify=False)
    return response.text

def mlooc_curl_post(post_data, url, ifurl='', UserAgent=UserAgent):
    headers = {
        'User-Agent': UserAgent,
        'Referer': ifurl,
        'X-FORWARDED-FOR': rand_ip(),
        'CLIENT-IP': rand_ip()
    }
    response = requests.post(url, data=post_data, headers=headers, verify=False)
    return response.text

def mlooc_curl_head(url, guise, UserAgent, cookie):
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Pragma': 'no-cache',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': UserAgent,
        'Cookie': cookie
    }
    response = requests.get(url, headers=headers, allow_redirects=False)
    return response.headers.get('Location', '')

def rand_ip():
    import random
    ip2id = round(random.uniform(60, 255))
    ip3id = round(random.uniform(60, 255))
    ip4id = round(random.uniform(60, 255))
    arr_1 = ["218", "218", "66", "66", "218", "218", "60", "60", "202", "204", "66", "66", "66", "59", "61", "60", "222", "221", "66", "59", "60", "60", "66", "218", "218", "62", "63", "64", "66", "66", "122", "211"]
    ip1id = random.choice(arr_1)
    return f"{ip1id}.{ip2id}.{ip3id}.{ip4id}"

def main(url, pwd='', type=''):
    if not url:
        return json.dumps({'code': 400, 'msg': '请输入URL'}, ensure_ascii=False, indent=4)

    url = 'https://www.lanzoup.com/' + url.split('.com/')[1]
    softInfo = mlooc_curl_get(url)

    if "文件取消分享了" in softInfo:
        return json.dumps({'code': 400, 'msg': '文件取消分享了'}, ensure_ascii=False, indent=4)

    softName = re.findall(r'style="font-size: 30px;text-align: center;padding: 56px 0px 20px 0px;">(.*?)</div>', softInfo)
    if not softName:
        softName = re.findall(r'<div class="n_box_3fn".*?>(.*?)</div>', softInfo)
    softFilesize = re.findall(r'<div class="n_filesize".*?>大小：(.*?)</div>', softInfo)
    if not softFilesize:
        softFilesize = re.findall(r'<span class="p7">文件大小：</span>(.*?)<br>', softInfo)
    if not softName:
        softName = re.findall(r'var filename = \'(.*?)\';', softInfo)
    if not softName:
        softName = re.findall(r'div class="b"><span>(.*?)</span></div>', softInfo)

    if "function down_p(){" in softInfo:
        if not pwd:
            return json.dumps({'code': 400, 'msg': '请输入分享密码'}, ensure_ascii=False, indent=4)
        segment = re.findall(r"skdklds = '(.*?)';", softInfo)
        post_data = {
            "action": 'downprocess',
            "sign": segment[0],
            "p": pwd
        }
        softInfo = mlooc_curl_post(post_data, "https://www.lanzoup.com/ajaxm.php", url)
        softName = [json.loads(softInfo)['inf']]
    else:
        link = re.findall(r'\n<iframe.*?name="[\s\S]*?"\ssrc="\/(.*?)"', softInfo)
        if not link:
            link = re.findall(r'<iframe.*?name="[\s\S]*?"\ssrc="\/(.*?)"', softInfo)
        ifurl = urljoin("https://www.lanzoup.com/", link[0])
        softInfo = mlooc_curl_get(ifurl)
        segment = re.findall(r"'sign':'(.*?)'", softInfo)
        post_data = {
            "action": 'downprocess',
            "signs": "?ctdf",
            "sign": segment[0]
        }
        softInfo = mlooc_curl_post(post_data, "https://www.lanzoup.com/ajaxm.php", ifurl)

    softInfo = json.loads(softInfo)
    if softInfo['zt'] != 1:
        return json.dumps({'code': 400, 'msg': softInfo['inf']}, ensure_ascii=False, indent=4)

    downUrl1 = softInfo['dom'] + '/file/' + softInfo['url']
    downUrl2 = mlooc_curl_head(downUrl1, "https://developer.lanzoug.com", UserAgent, "down_ip=1; expires=Sat, 16-Nov-2019 11:42:54 GMT; path=/; domain=.baidupan.com")
    downUrl = downUrl2 if downUrl2 else downUrl1
    downUrl = re.sub(r'/pid=(.*?.)&/', '', downUrl)

    if type != "down":
        return json.dumps({
            'code': 200,
            'msg': '解析成功',
            'name': softName[0] if softName else "",
            'filesize': softFilesize[0] if softFilesize else "",
            'downUrl': downUrl
        }, ensure_ascii=False, indent=4)
    else:
        return downUrl

if __name__ == "__main__":
    url = input("URL: ")
    result = main(url)
    result_dict = json.loads(result)
    print('=' * 20)
    if result_dict["code"] != 200:
        print("失败：" + str(result_dict))
        quit()
    print("状态：" + str(result_dict["code"]))
    print("文件名：" + result_dict["name"])
    print("文件大小：" + result_dict["filesize"])
    print("直链：" + result_dict["downUrl"])
