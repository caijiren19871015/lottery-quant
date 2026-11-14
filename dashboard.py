def force_sync_until_today(lottery_name):
    """智能断档补齐，并自动根据接口校准真实时间"""
    config = LOTTERY_MAP.get(lottery_name)
    if not config: return "未知彩种"
    
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://166w2.com/"}
    
    # 💡 第一步：探测接口真实的“今天”是哪天（防止电脑时间穿越）
    probe_url = ""
    if config["type"] == "dpc":
        # 传统彩，探测今天的日期
        probe_url = f"https://data.kai8.us/api/lottery/history_dpc/KAI166/{config['code']}/{datetime.today().strftime('%Y/%m/%d')}"
    else:
        probe_url = f"https://data.kai8.us/api/lottery/history_gpc/KAI166/{config['code']}/{datetime.today().strftime('%Y-%m-%d')}/23/30"
        
    true_latest_time = None
    try:
        res = requests.get(probe_url, headers=headers, timeout=5)
        if res.status_code == 200:
            records = res.json().get("data", {}).get("list", [])
            if records:
                # 拿到接口返回的真实最新时间
                true_latest_time = pd.to_datetime(records[0].get("draw_time")).date()
    except Exception:
        pass

    # 如果探测成功，用真实时间覆盖系统时间；如果失败，退回系统时间
    end_date = true_latest_time if true_latest_time else datetime.today().date()
    
    # 第二步：查库里最新的一期是哪天
    try:
        df_old = pd.read_sql(f"SELECT draw_time FROM lottery_data WHERE lottery_name = '{lottery_name}' ORDER BY draw_time DESC LIMIT 1", engine)
        if df_old.empty:
            start_date = end_date - timedelta(days=30) # 空库默认抓30天
        else:
            max_date = pd.to_datetime(df_old.iloc[0]['draw_time']).date()
            if max_date >= end_date:
                return f"✅ 已是最新 (本地最新为 {max_date}，官网最新为 {end_date})"
            start_date = max_date
    except Exception as e:
        return f"❌ 读取数据库失败: {e}"

    delta_days = (end_date - start_date).days + 1
    
    if delta_days > 60:
        start_date = end_date - timedelta(days=60)
        delta_days = 61
        limit_msg = " (因断档过久，为防封禁仅补齐最近60天)"
    else:
        limit_msg = ""
    
    all_new_data = []
    error_logs = []
    
    for day_offset in range(delta_days):
        target_date = start_date + timedelta(days=day_offset)
        y, m, d = target_date.strftime("%Y"), target_date.month, target_date.day
        
        if config["type"] == "dpc":
            url = f"https://data.kai8.us/api/lottery/history_dpc/KAI166/{config['code']}/{y}/{m}/{d}"
            try:
                res = requests.get(url, headers=headers, timeout=5)
                if res.status_code == 200:
                    for item in res.json().get("data", {}).get("list", []):
                        all_new_data.append({"lottery_name": lottery_name, "issue": str(item.get("issue")).strip(), "draw_time": item.get("draw_time"), "draw_code": item.get("draw_code")})
                else:
                    error_logs.append(f"{target_date} HTTP:{res.status_code}")
            except Exception as e:
                error_logs.append(f"{target_date} 异常:{str(e)[:20]}")
            time.sleep(0.02)
        else:
            for h in range(24):
                url = f"https://data.kai8.us/api/lottery/history_gpc/KAI166/{config['code']}/{y}-{m:02d}-{d:02d}/{h}/30"
                try:
                    res = requests.get(url, headers=headers, timeout=5)
                    if res.status_code == 200:
                        for item in res.json().get("data", {}).get("list", []):
                            all_new_data.append({"lottery_name": lottery_name, "issue": str(item.get("issue")).strip(), "draw_time": item.get("draw_time"), "draw_code": item.get("draw_code")})
                except Exception: pass
                time.sleep(0.01)

    if not all_new_data:
        err_msg = "; ".join(error_logs[:3]) if error_logs else "接口未返回任何数据"
        return f"⚠️ 抓取失败: {err_msg}"

    df_new = pd.DataFrame(all_new_data)
    df_new['issue'] = df_new['issue'].astype(str).str.strip()
    df_new['draw_time'] = pd.to_datetime(df_new['draw_time'], errors='coerce').dt.tz_localize(None)
    df_new = df_new.dropna(subset=['draw_time']).drop_duplicates(subset=['lottery_name', 'issue'], keep='first')

    try:
        existing = pd.read_sql(f"SELECT issue FROM lottery_data WHERE lottery_name = '{lottery_name}'", engine)['issue'].astype(str).str.strip().tolist()
        df_new = df_new[~df_new['issue'].isin(existing)]
        if not df_new.empty:
            df_new.to_sql('lottery_data', engine, if_exists='append', index=False)
            return f"✅ 补齐完成！官网最新数据为 {end_date}，本次新增 {len(df_new)} 条。{limit_msg}"
        else:
            return f"已是最新 (官网最新日期为 {end_date}，数据均已存在){limit_msg}"
    except Exception as e:
        return f"❌ 写入数据库失败: {e}"