def force_sync_until_today(lottery_name):
    """智能断档补齐：自动检测本地库最新日期，补齐到今天"""
    config = LOTTERY_MAP.get(lottery_name)
    if not config: return "未知彩种"

    end_date = datetime.today().date()

    # 1. 查库里最新的一期是哪天
    try:
        df_old = pd.read_sql(f"SELECT draw_time FROM lottery_data WHERE lottery_name = '{lottery_name}' ORDER BY draw_time DESC LIMIT 1", engine)
        if df_old.empty:
            start_date = end_date - timedelta(days=30)  # 空库默认抓30天
        else:
            max_date = pd.to_datetime(df_old.iloc[0]['draw_time']).date()
            # ✅ 只有 香港六合彩 / 快乐8六合彩 才跳过"日期短路"，并回扫 max_date - 1 天
            #    其他彩种（含高频彩）走原逻辑，行为 100% 不变
            if lottery_name in ("香港六合彩", "快乐8六合彩"):
                start_date = max_date - timedelta(days=1)
            else:
                if max_date >= end_date:
                    return "✅ 已是最新 (本地已有今天的开奖数据)"
                start_date = max_date
    except Exception as e:
        return f"❌ 读取数据库失败: {e}"

    delta_days = (end_date - start_date).days + 1

    # 💡 安全阀：为了防止断档半年后点击导致请求爆炸被封IP，最多往回抓60天
    if delta_days > 60:
        start_date = end_date - timedelta(days=60)
        delta_days = 61
        limit_msg = " (因断档过久，为防封禁仅补齐最近60天)"
    else:
        limit_msg = ""

    all_new_data = []
    error_logs = []
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://166w2.com/"}

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
            # 高频彩（宾果/加拿大）分支：原样保留，一行不改
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
        err_msg = "; ".join(error_logs[:3]) if error_logs else "接口未返回任何数据(可能官方还没更新)"
        return f"⚠️ 抓取失败: {err_msg}"

    df_new = pd.DataFrame(all_new_data)
    df_new['issue'] = df_new['issue'].astype(str).str.strip()
    df_new['draw_time'] = pd.to_datetime(df_new['draw_time'], errors='coerce').dt.tz_localize(None)
    df_new = df_new.dropna(subset=['draw_time']).drop_duplicates(subset=['lottery_name', 'issue'], keep='first')

    try:
        existing = pd.read_sql(f"SELECT issue FROM lottery_data WHERE lottery_name = '{lottery_name}'", engine)['issue'].astype(str).str.strip().tolist()
        df_new = df_new[~df_new['issue'].isin(existing)]

        # 接口本次返回的最新期号（用于给"已是最新"补上真实期号，避免误判）
        api_latest_issue = pd.DataFrame(all_new_data)['issue'].astype(str).str.strip().max()

        if not df_new.empty:
            df_new.to_sql('lottery_data', engine, if_exists='append', index=False)
            return f"✅ 补齐完成！扫描了 {start_date} 至 {end_date}，新增 {len(df_new)} 条数据。最新一期: {df_new.iloc[-1]['issue']}{limit_msg}"
        else:
            # ✅ 区分：接口有数据但全已存在 vs 接口本次没返回
            return f"✅ 已是最新（接口最新期 {api_latest_issue} 已入库，扫描 {start_date} 至 {end_date}）{limit_msg}"
    except Exception as e:
        return f"❌ 写入数据库失败: {e}"