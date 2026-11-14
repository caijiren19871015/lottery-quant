def fetch_135ka(code, page=1):
    """专门抓取 135ka.com 的按序接口 (加强版)"""
    url = "https://135ka.com/index.php/index/lhc/getdata2.html"
    
    # 💡 极其关键的伪装 Headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://135ka.com/index.php/index/lhc.html?t={code}",
        "X-Requested-With": "XMLHttpRequest",  # 💡 必须加！告诉服务器这是 Ajax 请求
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://135ka.com"
    }
    # 参数格式
    payload = {"lotCode": code, "ltype": 3, "date": "", "page": page}
    
    try:
        # 使用 Session 保持连接，模拟真实浏览器
        session = requests.Session()
        res = session.post(url, headers=headers, data=payload, timeout=10)
        
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get("status") == 1: # 成功状态
                return res_json.get("data", [])
            else:
                # 如果接口返回错误状态，把消息抛出来
                print(f"135ka 接口报错: {res_json.get('msg')}")
                return []
        else:
            print(f"HTTP 错误码: {res.status_code}")
            return []
    except Exception as e:
        print(f"请求发生异常: {e}")
        return []