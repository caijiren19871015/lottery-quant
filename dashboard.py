def force_sync_until_today(lottery_name):
    """智能断档补齐，并带有备用接口自动切换"""
    config = LOTTERY_MAP.get(lottery_name)
    if not config: return "未知彩种"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://166kai.com/"
    }
    
    # 💡 1. 探测真实最新时间（绕过主接口的假数据）
    # 采用国内极速备用接口探测
    true_latest_time = None
    try:
        today_str = datetime.today().strftime("%Y-%m-%d")
        probe_url = f"https://api.api68.com/pks/getPksHistoryList.do?date={today_str}&lotCode={config['code']}"
        res = requests.get(probe_url, headers=headers, timeout=5)
        if res.status_code == 200:
            records = res.json().get("result", {}).get("data", [])
            if records:
                true_latest_time = pd.to_datetime(records[0].get("preDrawTime")).date()
    except Exception:
        pass

    # 如果备用接口也探测不到，只能信系统时间
    end_date = true_latest_time if true_latest_time else datetime.today().date()
    
    # 💡 2. 查库里的最新日期
    try:
        df_old = pd.read_sql(f"SELECT draw_time FROM lottery_data WHERE lottery_name = '{lottery_name}' ORDER BY draw_time DESC LIMIT 1", engine)
        if df_old.empty:
            start_date = end_date - timedelta(days=30) # 空库默认抓30天
        else:
            max_date = pd.to_datetime(df_old.iloc[0]['draw_time']).date()
            if max_date >= end_date:
                return f"✅ 已是最新 (本地最新: {max_date}，接口最新: {end_date})"
            start_date = max_date
    except Exception as e:
        return f"❌ 读取数据库失败: {e}"

    delta_days = (end_date - start_date).days + 1
    if delta_days > 60:
        start_date = end_date - timedelta(days=60)
        delta_days = 61
        limit_msg = " (因断档过久，仅补齐最近60天)"
    else:
        limit_msg = ""
    
    all_new_data = []
    error_logs = []
    
    # 💡 3. 开始补全数据（优先用 API68，失败再切回 KAI8）
    for day_offset in range(delta_days):
        target_date = start_date + timedelta(days=day_offset)
        y, m, d = target_date.strftime("%Y"), target_date.month, target_date.day
        date_str_dash = target_date.strftime("%Y-%m-%d")
        date_str_slash = target_date.strftime("%Y/%m/%d")
        
        day_data = []
        
        # 尝试方案A：API68 极速备用接口
        if config["type"] == "dpc":
            url68 = f"https://api.api68.com/pks/getPksHistoryList.do?date={date_str_dash}&lotCode={config['code']}"
            try:
                res = requests.get(url68, headers=headers, timeout=5)
                if res.status_code == 200:
                    for item in res.json().get("result", {}).get("data", []):
                        day_data.append({
                            "lottery_name": lottery_name, 
                            "issue": str(item.get("preDrawIssue")).strip(), 
                            "draw_time": item.get("preDrawTime"), 
                            "draw_code": item.get("preDrawCode")
                        })
            except Exception: pass
            
        # 尝试方案B：如果方案A没数据，退回主接口
        if not day_data:
            if config["type"] == "dpc":
                url_kai8 = f"https://data.kai8.us/api/lottery/history_dpc/KAI166/{config['code']}/{y}/{m}/{d}"
                try:
                    res = requests.get(url_kai8, headers=headers, timeout=5)
                    if res.status_code == 200:
                        for item in res.json().get("data", {}).get("list", []):
                            day_data.append({
                                "lottery_name": lottery_name, 
                                "issue": str(item.get("issue")).strip(), 
                                "draw_time": item.get("draw_time"), 
                                "draw_code": item.get("draw_code")
                            })
                except Exception as e:
                    error_logs.append(f"{target_date} 异常:{str(e)[:20]}")
            else:
                # 高频彩依然用 KAI8 按小时循环
                for h in range(24):
                    url_kai8 = f"https://data.kai8.us/api/lottery/history_gpc/KAI166/{config['code']}/{y}-{m:02d}-{d:02d}/{h}/30"
                    try:
                        res = requests.get(url_kai8, headers=headers, timeout=5)
                        if res.status_code == 200:
                            for item in res.json().get("data", {}).get("list", []):
                                day_data.append({
                                    "lottery_name": lottery_name, 
                                    "issue": str(item.get("issue")).strip(), 
                                    "draw_time": item.get("draw_time"), 
                                    "draw_code": item.get("draw_code")
                                })
                    except Exception: pass
                    time.sleep(0.01)
        
        # 过滤掉 API68 可能返回的旧缓存假数据（强制让日期匹配）
        for row in day_data:
            if pd.to_datetime(row["draw_time"]).date() == target_date:
                all_new_data.append(row)
                
        time.sleep(0.02)

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
            return f"✅ 补齐完成！接口真实最新日期: {end_date}，本次新增 {len(df_new)} 条。{limit_msg}"
        else:
            return f"已是最新 (接口真实最新日期: {end_date}，数据均已存在){limit_msg}"
    except Exception as e:
        return f"❌ 写入数据库失败: {e}"