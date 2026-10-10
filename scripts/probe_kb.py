#!/usr/bin/env python3
"""[진단 전용] KB부동산 시계열 자동 수집 가능성 점검 — PublicDataReader(비공식 KB 데이터허브 래퍼).
산출물(docs/)을 건드리지 않고 로그만 남긴다. 항상 정상 종료. 결과를 보고 build_data.py에 자동 수집 경로를 연결한다."""
import sys, inspect, traceback

def main():
    try:
        import PublicDataReader as pdr
    except Exception as e:
        print("PublicDataReader import 실패:", type(e).__name__, e)
        return
    print("PublicDataReader", getattr(pdr, "__version__", "?"))
    print("KB 관련 이름:", [n for n in dir(pdr) if "kb" in n.lower()])
    K = getattr(pdr, "Kbland", None)
    if K is None:
        print("Kbland 클래스 없음")
        return
    api = K()
    meths = [n for n in dir(api) if not n.startswith("_") and callable(getattr(api, n))]
    for n in meths:
        f = getattr(api, n)
        try:
            print("-", n, inspect.signature(f))
            print("    ", (inspect.getdoc(f) or "").replace("\n", " ")[:400])
        except Exception:
            pass
    # 가격지수 계열 메서드를 추정 호출(실패해도 무방) — 주간/월간 매매 전국
    for n in meths:
        if "price_index" in n or "index" in n:
            f = getattr(api, n)
            for kw in ({}, {"월간주간구분코드": "01", "매물종별구분": "01", "매매전세코드": "01", "지역코드": "1100000000", "기간": 12}):
                try:
                    df = f(**kw)
                    print(f"호출 성공 {n}({kw}):", getattr(df, "shape", None))
                    try:
                        print(df.head(3).to_string()); print(df.tail(3).to_string())
                    except Exception:
                        print(repr(df)[:500])
                    break
                except Exception as e:
                    print(f"호출 실패 {n}({kw}): {type(e).__name__}: {str(e)[:200]}")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
    sys.exit(0)
