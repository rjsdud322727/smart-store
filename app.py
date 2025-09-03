import oracledb
from flask import Flask, jsonify, render_template, request, send_file
import sys
import traceback
import barcode
from barcode.writer import ImageWriter
import os
import re
import pandas as pd
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import random
import xgboost as xgb

app = Flask(__name__)

# 최근 구매한 상품을 저장할 리스트
recent_purchases = []

def generate_barcode_image(barcode_number):
    try:
        # 바코드 객체 생성 + 이미지 저장 옵션
        ean = barcode.get("ean13", barcode_number, writer=ImageWriter())
        
        # 바코드 이미지로 저장
        filename = ean.save(os.path.join('static', 'barcodes', barcode_number))
        return f'/static/barcodes/{barcode_number}.png'
    except Exception as e:
        print(f"바코드 생성 오류: {str(e)}")
        raise

def get_db_connection():
    # 데이터베이스 연결 설정
    conn = oracledb.connect(
        user='system',          # 사용자 이름
        password='oradb1',     # 비밀번호
        dsn='localhost/xe'     # DSN (데이터베이스 서비스 이름)
    )
    return conn

@app.route('/')
def index():
    return render_template('code.html')

@app.route('/api/products', methods=['GET'])
def get_products():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT BARCODE, PRODUCT_NAME, EXPIRATION_DATE, QUANTITY, PRICE FROM PRODUCTS")
        
        products = []
        for row in cursor:
            product = {
                'barcode': row[0],
                'name': row[1],
                'expiration_date': row[2].strftime('%Y-%m-%d') if row[2] else '',
                'quantity': row[3],
                'price': row[4]
            }
            products.append(product)
        
        cursor.close()
        conn.close()
        return jsonify(products)
    except Exception as e:
        print("오류 발생:", str(e))  # 오류 메시지 출력
        print("상세 오류:", traceback.format_exc())  # 상세 오류 출력
        return jsonify({'error': str(e)}), 500

@app.route('/generate_barcode', methods=['POST'])
def generate_barcode():
    try:
        data = request.json
        barcode_number = data.get('barcode')
        
        if not barcode_number:
            return jsonify({'error': '바코드 번호가 필요합니다.'}), 400
        
        # 바코드 이미지 생성
        image_path = generate_barcode_image(barcode_number)
        
        return jsonify({
            'message': '바코드 생성 완료',
            'filename': image_path
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/products/alerts', methods=['GET'])
def get_alert_products():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT BARCODE, PRODUCT_NAME, EXPIRATION_DATE, QUANTITY, PRICE FROM PRODUCTS
            WHERE EXPIRATION_DATE <= SYSDATE + 3 AND QUANTITY > 0
        """)
        products = []
        for row in cursor:
            product = {
                'barcode': row[0] if row[0] else '정보 없음',
                'name': row[1] if row[1] else '정보 없음',
                'expiration_date': row[2].strftime('%Y-%m-%d') if row[2] else '정보 없음',
                'quantity': row[3] if row[3] else 0,
                'price': row[4] if row[4] else 0
            }
            expiration_date = row[2]
            if expiration_date:
                # 날짜만 비교 (시간 무시)
                days_left = (expiration_date.date() - datetime.now().date()).days
                if days_left == 3:
                    product['price'] = round(product['price'] * 0.9)
                    product['discount'] = '10%'
                elif days_left == 2:
                    product['price'] = round(product['price'] * 0.8)
                    product['discount'] = '20%'
                elif days_left == 1:
                    product['price'] = round(product['price'] * 0.7)
                    product['discount'] = '30%'
                else:
                    product['discount'] = '없음'
            else:
                product['discount'] = '없음'
            products.append(product)
        cursor.close()
        conn.close()
        return jsonify(products)
    except Exception as e:
        print(f"오류 발생: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/products/expired', methods=['GET'])
def get_expired_products():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # 유통기한이 지난 제품의 상품명만 조회
        cursor.execute("""
            SELECT 
                BARCODE AS product_barcode, 
                PRODUCT_NAME AS product_name, 
                EXPIRATION_DATE AS expiration_date 
            FROM PRODUCTS
            WHERE EXPIRATION_DATE < SYSDATE OR QUANTITY <= 0
        """)
        products = []
        for row in cursor:
            product = {
                'barcode': row[0] if row[0] else '',
                'name': row[1] if row[1] else '',
                'expiration_date': row[2].strftime('%Y-%m-%d') if row[2] else ''
            }
            products.append(product)
        cursor.close()
        conn.close()
        return jsonify(products)  # 상품명 리스트 반환
    except Exception as e:
        print(f"오류 발생: {str(e)}", file=sys.stderr)
        return jsonify({'error': str(e)}), 500

@app.route('/api/add_product', methods=['POST'])
def add_product():
    data = request.json
    name = data.get('name')
    expiration = data.get('expiration')
    barcode_number = data.get('barcode')

    # DB에 상품 추가
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT INTO PRODUCTS (BARCODE, PRODUCT_NAME, EXPIRATION_DATE, QUANTITY, PRICE) VALUES (:barcode, :name, TO_DATE(:expiration, 'YYYY-MM-DD'), :quantity, :price)",
            {
                'barcode': barcode_number,
                'name': name,
                'expiration': expiration,
                'quantity': data.get('quantity', 0),  # 수량 추가
                'price': data.get('price', 0)  # 가격 추가
            }
        )
        
        # 바코드 이미지 생성
        ean = barcode.get("ean13", barcode_number, writer=ImageWriter())
        filename = ean.save(os.path.join('static', 'barcodes', barcode_number))  # 바코드 이미지 저장
        
        conn.commit()  # 변경 사항 커밋
        return jsonify({'message': '상품 및 바코드 추가 완료', 'barcode_image': filename})
    
    except Exception as e:
        conn.rollback()  # 오류 발생 시 롤백
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/upload_excel', methods=['POST'])
def upload_excel():
    file = request.files['file']
    try:
        df = pd.read_excel(file, dtype={'barcode': str})  # 엑셀 파일 읽기
    except Exception as e:
        print(f"엑셀 파일 읽기 오류: {str(e)}")
        return jsonify({'error': '엑셀 파일을 읽는 데 오류가 발생했습니다.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    
    for _, row in df.iterrows():
        try:
            # 날짜 형식 변환
            expiration_value = row['expiration_date']
            
            # 날짜가 문자열 형식인 경우
            if isinstance(expiration_value, str):
                expiration_date = datetime.strptime(expiration_value, '%Y-%m-%d')  # 변환
            # 날짜가 숫자 형식인 경우
            elif isinstance(expiration_value, (int, float)):
                expiration_date = datetime(1899, 12, 30) + pd.to_timedelta(expiration_value, unit='D')
            else:
                continue  # 날짜 형식이 올바르지 않은 경우 건너뜀
            
            # 데이터베이스에 삽입
            cursor.execute(
                """
                INSERT INTO PRODUCTS 
                (BARCODE, PRODUCT_NAME, EXPIRATION_DATE, QUANTITY, PRICE)
                VALUES (:barcode, :name, TO_DATE(:expiration, 'YYYY-MM-DD'), :quantity, :price)
                """,
                {
                    'barcode': row['barcode'],
                    'name': row['name'],
                    'expiration': expiration_date.strftime('%Y-%m-%d'),  # 변환된 날짜 사용
                    'quantity': row['quantity'],
                    'price': row['price']
                }
            )
        except Exception as e:
            print(f"데이터 삽입 오류 (바코드: {row.get('barcode', '알 수 없음')}): {str(e)}")
            continue  # 오류가 발생한 경우 다음 행으로 넘어감

    conn.commit()  # 변경 사항 커밋
    cursor.close()
    conn.close()
    
    # 엑셀 파일 업로드 후 데이터베이스에서 최신 데이터 가져오기
    return jsonify({'message': '엑셀 파일에서 데이터가 추가되었습니다.'})

@app.route('/barcode')
def barcode_list():
    return render_template('barcode.html')

@app.route('/api/discard_product/<int:product_id>', methods=['DELETE'])
def discard_product(product_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM PRODUCTS WHERE PRODUCT_ID = :id", {'id': product_id})
        conn.commit()
        return jsonify({'message': '제품이 폐기되었습니다.'})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/.well-known/appspecific/com.chrome.devtools.json')
def devtools_json():
    return jsonify({"message": "Chrome DevTools is ready."})

@app.route('/api/products/<barcode>', methods=['GET'])
def get_product_by_barcode(barcode):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT BARCODE, PRODUCT_NAME, EXPIRATION_DATE, QUANTITY, PRICE FROM PRODUCTS WHERE BARCODE = :barcode", {'barcode': barcode})
        row = cursor.fetchone()
        if row:
            product = {
                'barcode': row[0],
                'name': row[1],
                'expiration_date': row[2],
                'quantity': row[3],
                'price': row[4]
            }
            return jsonify(product)
        else:
            return jsonify({'error': '제품을 찾을 수 없습니다.'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# AI 구매 로직
def ai_purchase_simulation():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # 재고가 있는 상품 조회
        cursor.execute("SELECT BARCODE, PRODUCT_NAME, PRICE, EXPIRATION_DATE FROM PRODUCTS WHERE QUANTITY > 0")
        products = cursor.fetchall()
        
        if not products:
            print("구매할 상품이 없습니다.")
            return
        
        num_to_purchase = random.randint(1, 3)
        selected_products = random.sample(products, min(num_to_purchase, len(products)))

        for product in selected_products:
            barcode = product[0]
            product_name = product[1]
            price = product[2]
            expiration_date = product[3]
            cursor.execute("UPDATE PRODUCTS SET QUANTITY = QUANTITY - 1 WHERE BARCODE = :barcode AND QUANTITY > 0", {'barcode': barcode})
            print(f"AI가 상품 바코드 {barcode}를 구매했습니다.")
            
            # 최근 구매한 상품 리스트에 추가 (구매 시각 추가)
            recent_purchases.append({
                'barcode': barcode,
                'name': product_name,
                'price': price,
                'purchase_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })

            # SALE 테이블에 판매 기록 추가
            cursor.execute(
                "INSERT INTO SALE (ID, BARCODE, PRODUCT_NAME, EXPIRATION_DATE, QUANTITY, PRICE, SALE_DATE) "
                "VALUES (SALE_SEQ.NEXTVAL, :barcode, :product_name, :expiration_date, :quantity, :price, SYSDATE)",
                {
                    'barcode': barcode,
                    'product_name': product_name,
                    'expiration_date': expiration_date,
                    'quantity': 1,
                    'price': price
                }
            )
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"구매 처리 중 오류 발생: {str(e)}")
    finally:
        cursor.close()
        conn.close()

# 스케줄러 설정
scheduler = BackgroundScheduler()
scheduler.add_job(ai_purchase_simulation, 'interval', hours=1)  # 매 1시간마다 실행
scheduler.start()

@app.route('/api/recent_purchases', methods=['GET'])
def get_recent_purchases():
    try:
        # recent_purchases 리스트를 반환
        return jsonify(recent_purchases)
    except Exception as e:
        print(f"오류 발생: {str(e)}")  # 오류 로그
        return jsonify({'error': '내부 서버 오류', 'details': str(e)}), 500

@app.route('/api/ai_purchase', methods=['POST'])
def ai_purchase():
    ai_purchase_simulation()  # AI 구매 로직 실행
    return jsonify({'message': 'AI가 제품을 구매했습니다.'})

@app.route('/receipts')
def receipts():
    return render_template('receipts.html')  # receipts.html 파일을 생성하여 영수증 조회 페이지를 구성합니다.

@app.route('/api/sell_product', methods=['POST'])
def sell_product():
    data = request.json
    barcode = data.get('barcode')
    quantity = data.get('quantity')

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT PRODUCT_NAME, EXPIRATION_DATE, PRICE FROM PRODUCTS WHERE BARCODE = :barcode", {'barcode': barcode})
        product = cursor.fetchone()

        if product:
            product_name = product[0]
            expiration_date = product[1]
            price = product[2]

            # SALE 테이블에 판매 기록 추가
            cursor.execute(
                "INSERT INTO SALE (ID, BARCODE, PRODUCT_NAME, EXPIRATION_DATE, QUANTITY, PRICE, SALE_DATE) "
                "VALUES (SALE_SEQ.NEXTVAL, :barcode, :product_name, :expiration_date, :quantity, :price, SYSDATE)",
                {
                    'barcode': barcode,
                    'product_name': product_name,
                    'expiration_date': expiration_date,
                    'quantity': quantity,
                    'price': price
                }
            )
            conn.commit()
            return jsonify({'message': '판매 기록이 저장되었습니다.'})
        else:
            return jsonify({'error': '제품을 찾을 수 없습니다.'}), 404
    except Exception as e:
        print(f"오류 발생: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/daily_sales', methods=['GET'])
def get_daily_sales():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                TO_CHAR(SALE_DATE, 'YYYY-MM-DD') AS sale_date, 
                SUM(QUANTITY * PRICE) AS total_revenue 
            FROM SALE
            WHERE SALE_DATE >= TRUNC(SYSDATE) - 30  -- Last 30 days
            GROUP BY TO_CHAR(SALE_DATE, 'YYYY-MM-DD')
            ORDER BY sale_date
        """)
        sales_data = cursor.fetchall()
        cursor.close()
        conn.close()
        
        # Convert to JSON format
        result = [{'sale_date': row[0], 'total_revenue': row[1]} for row in sales_data]
        return jsonify(result)
    except Exception as e:
        print(f"Error occurred: {str(e)}")  # Log the error
        return jsonify({'error': 'Internal Server Error', 'details': str(e)}), 500

@app.route('/api/check_and_generate_restock_excel', methods=['POST'])
def check_and_generate_restock_excel():
    try:
        data = request.get_json()
        products = data.get('products', [])
        if not products:
            return jsonify({'error': '입고할 제품이 없습니다.'}), 400

        # 바코드, 유통기한 임의 생성
        for product in products:
            # 바코드가 없으면 13자리 임의 숫자 생성
            if not product.get('barcode'):
                product['barcode'] = ''.join([str(random.randint(0, 9)) for _ in range(13)])
            # 유통기한이 없으면 오늘로부터 30일 뒤로 임의 설정
            if not product.get('expiration_date'):
                product['expiration_date'] = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')

        output_path = r"C:\Users\마하영\OneDrive\바탕 화면\편의점\입고 엑셀파일\입고_필요_제품.xlsx"
        df = pd.DataFrame(products)
        try:
            df.to_excel(output_path, index=False)
        except PermissionError as e:
            print(f"파일 저장 오류: {str(e)}")
            return jsonify({'error': '파일 저장 중 오류 발생. 파일이 열려 있거나 권한이 없습니다.'}), 500

        return jsonify({'message': '입고할 주문 엑셀 파일이 생성되었습니다.'}), 200
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/restock_list')
def restock_list():
    return render_template('restock_list.html')

@app.route('/api/get_restock_list', methods=['GET'])
def get_restock_list():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # 수량이 0인 제품 조회
        cursor.execute("""
            SELECT PRODUCT_NAME, PRICE 
            FROM PRODUCTS 
            WHERE QUANTITY = 0 AND EXPIRATION_DATE >= SYSDATE
        """)
        products = []
        for row in cursor:
            product = {
                'name': row[0],  # PRODUCT_NAME
                'price': row[1],
                'quantity': 20  # 입고할 수량을 20으로 설정
            }
            products.append(product)
        cursor.close()
        conn.close()
        return jsonify(products)
    except Exception as e:
        print(f"Error occurred: {str(e)}")  # 오류 메시지 출력
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate_restock_excel', methods=['POST'])
def generate_restock_excel():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 수량이 0개이거나 유통기한이 지난 제품 조회
        cursor.execute("""
            SELECT BARCODE, PRODUCT_NAME, EXPIRATION_DATE FROM PRODUCTS
            WHERE QUANTITY <= 0 OR EXPIRATION_DATE < SYSDATE
        """)
        
        products = []
        for row in cursor:
            product = {
                'barcode': row[0] if row[0] else '',
                'name': row[1] if row[1] else '',
                'expiration_date': row[2].strftime('%Y-%m-%d') if row[2] else ''
            }
            products.append(product)
        
        cursor.close()
        conn.close()

        if not products:
            return jsonify({'message': '입고할 제품이 없습니다.'}), 200

        # 엑셀 파일 생성
        df = pd.DataFrame(products)
        df.to_excel("C:\\Users\\마하영\\OneDrive\\바탕 화면\\편의점\\입고 엑셀파일\\입고_필요_제품.xlsx", index=False)  # 특정 경로에 엑셀 파일로 저장

        return jsonify({'message': '입고할 주문 엑셀 파일이 생성되었습니다.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/sales_statistics')
def sales_statistics():
    return render_template('sales_statistics.html')

@app.route('/api/monthly_sales', methods=['GET'])
def get_monthly_sales():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            TO_CHAR(SALE_DATE, 'YYYY-MM') AS month, 
            SUM(QUANTITY) AS total_quantity, 
            AVG(PRICE) AS price
        FROM SALE
        JOIN PRODUCTS ON SALE.BARCODE = PRODUCTS.BARCODE
        WHERE SALE_DATE >= TRUNC(SYSDATE, 'YYYY') -- 이번 년도부터의 데이터
        GROUP BY TO_CHAR(SALE_DATE, 'YYYY-MM')
        ORDER BY month
    """)
    sales_data = cursor.fetchall()
    cursor.close()
    conn.close()
    
    # 결과를 JSON 형식으로 변환
    result = [{'month': row[0], 'total_quantity': row[1], 'price': row[2]} for row in sales_data]
    return jsonify(result)

@app.route('/api/discard_products', methods=['POST'])
def discard_products():
    data = request.get_json()
    barcodes = data.get('barcodes', [])
    if not barcodes:
        return jsonify({'error': '폐기할 바코드가 없습니다.'}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # 여러 바코드 삭제
        cursor.executemany(
            "DELETE FROM PRODUCTS WHERE BARCODE = :1",
            [(barcode,) for barcode in barcodes]
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': f'{len(barcodes)}개 제품이 폐기되었습니다.'})
    except Exception as e:
        print(f"폐기 처리 오류: {str(e)}")
        return jsonify({'error': str(e)}), 500

def analyze_daily_sales_for_recommendation():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 최근 7일간의 판매 데이터 조회
        cursor.execute("""
            SELECT 
                PRODUCT_NAME,
                SUM(QUANTITY) as total_sold,
                AVG(PRICE) as avg_price,
                COUNT(DISTINCT TO_CHAR(SALE_DATE, 'YYYY-MM-DD')) as days_sold
            FROM SALE
            WHERE SALE_DATE >= TRUNC(SYSDATE) - 7
            GROUP BY PRODUCT_NAME
            ORDER BY total_sold DESC
        """)
        
        sales_data = cursor.fetchall()
        
        # 현재 재고 상태 조회
        cursor.execute("""
            SELECT PRODUCT_NAME, QUANTITY
            FROM PRODUCTS
            WHERE EXPIRATION_DATE >= SYSDATE
        """)
        
        inventory_data = {row[0]: row[1] for row in cursor.fetchall()}
        
        recommendations = []
        for product in sales_data:
            name, total_sold, avg_price, days_sold = product
            current_stock = inventory_data.get(name, 0)
            
            # 판매 추세 분석
            daily_avg = total_sold / days_sold if days_sold > 0 else 0
            
            # 재고가 3일치 판매량보다 적으면 추천
            if current_stock < (daily_avg * 3):
                recommendations.append({
                    'name': name,
                    'current_stock': current_stock,
                    'daily_avg_sales': round(daily_avg, 1),
                    'recommended_quantity': max(20, int(daily_avg * 3 - current_stock)),
                    'avg_price': round(avg_price, 0)
                })
        
        cursor.close()
        conn.close()
        return recommendations
    except Exception as e:
        print(f"판매 분석 중 오류 발생: {str(e)}")
        return []

def generate_recommendation_explanation(recommendations):
    try:
        if not recommendations:
            return "현재 입고가 필요한 상품이 없습니다."
            
        explanation = "입고 추천 분석 결과:\n\n"
        
        for item in recommendations:
            explanation += f"• {item['name']}:\n"
            explanation += f"  - 현재 재고: {item['current_stock']}개\n"
            explanation += f"  - 일평균 판매량: {item['daily_avg_sales']}개\n"
            explanation += f"  - 추천 입고량: {item['recommended_quantity']}개\n"
            explanation += f"  - 입고 필요 이유: 현재 재고가 3일치 판매량보다 부족합니다.\n\n"
        
        explanation += "\n※ 입고 시 참고사항:\n"
        explanation += "1. 추천 수량은 최소 20개 이상입니다.\n"
        explanation += "2. 재고는 3일치 판매량을 기준으로 계산됩니다.\n"
        explanation += "3. 실제 입고 시에는 시즌성과 특별 이벤트를 고려하세요."
        
        return explanation
    except Exception as e:
        print(f"설명 생성 중 오류 발생: {str(e)}")
        return "분석 중 오류가 발생했습니다."

@app.route('/api/daily_best_sellers', methods=['GET'])
def daily_best_sellers():
    try:
        # 추천 데이터 생성
        recommendations = analyze_daily_sales_for_recommendation()
        explanation = generate_recommendation_explanation(recommendations)
        
        return jsonify({
            'recommendations': recommendations,
            'explanation': explanation
        })
    except Exception as e:
        print(f"데이터 조회 중 오류 발생: {str(e)}")
        return jsonify({'error': str(e)}), 500

# 스케줄러에 일일 AI 추천 작업 추가
scheduler.add_job(analyze_daily_sales_for_recommendation, 'cron', hour=0)  # 매일 자정에 실행

if __name__ == '__main__':
    # 바코드 이미지를 저장할 디렉토리 생성
    os.makedirs('static/barcodes', exist_ok=True)
    app.run(debug=True, host='0.0.0.0', port=5000) 