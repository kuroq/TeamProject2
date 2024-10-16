from fastapi import FastAPI, HTTPException, File, UploadFile, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, constr, ValidationError
from typing import Optional, List
from databases import Database
import logging
from google.cloud import speech_v1p1beta1 as speech
from google.oauth2 import service_account
import openai
import wave
from pydub import AudioSegment
import io
from starlette.middleware.sessions import SessionMiddleware
import traceback
from datetime import datetime
import json
import requests
from bs4 import BeautifulSoup
import aiohttp
import asyncio
from urllib.parse import quote_plus, parse_qs, urlparse
import googlemaps
from authlib.integrations.starlette_client import OAuth
from starlette.config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key='ranbu')

app.add_middleware(
    CORSMiddleware,
    # allow_origins=origins,    # 許可された出所リスト
    allow_origins=["*"],    # すべてのドメインを許可
    allow_credentials=True,
    allow_methods=["*"],    # すべてのHTTPメソッドを許可 (GET, POST, PUTなど)
    allow_headers=["*"],    # すべてのヘッダーを許可
)

# ChatGPT API キー設定
# セキュリティ通知 OpenAI APIキーが含まれているこの部分は、セキュリティ上の理由で削除されました。
openai.api_key = "セキュリティのために削除しました。" # 
#"過去のOpenAI APIキーも削除しました。" 

# データモデル定義
class CTextRequest(BaseModel):
    userId: int  # TextTableのuserId

# Google Cloud STT クライアント設定
stt_credentials = service_account.Credentials.from_service_account_file('/home/Seigakushakorea/セキュリティのために削除しました。')
stt_client = speech.SpeechClient(credentials=stt_credentials) 

# Google Maps API キー設定
gmaps = googlemaps.Client(key='セキュリティのために削除しました。')

# OAuthの設定
config = Config(environ={"GOOGLE_CLIENT_ID": "セキュリティのために削除しました。",
                         "GOOGLE_CLIENT_SECRET": "セキュリティのために削除しました。"})
oauth = OAuth(config)

# データベース設定 
DATABASE_URL = "mysql://セキュリティ上の理由で削除されました。"

 
database = Database(DATABASE_URL)

# LoginDataモデル定義
class LoginData(BaseModel):
    email: str
    pw: str


# 会員登録に使用するデータモデル定義
class registerData(BaseModel):
    name: constr(min_length=1)  # フィールドは文字列、最小長は1文字以上
    email: EmailStr
    pw: constr(min_length=8, max_length=20)  # フィールドは文字列、最小長は8文字以上
    gender: int
    birthDate: str
    phone: constr(min_length=1)  # フィールドは文字列、最小長は1文字以上
    mkt: int  # マーケティング受信同意のデフォルト値は「No」

# パスワード変更に使用するデータモデル定義
class changeData(BaseModel):
    email: EmailStr
    current_pw: constr(min_length=8, max_length=20)
    new_pw: constr(min_length=8, max_length=20)

class TextRequest(BaseModel):
    transcript: str
    #userId: int # 予備  
    
class ImageRequest(BaseModel):
    prompt: str
    user_id: Optional[int] = None


@app.on_event("startup")
async def startup():
    await database.connect()

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()

@app.post("/login")
async def validate_and_fetch_data(data: LoginData, request: Request):
    input_email = data.email
    input_pw = data.pw

    # データベースでユーザーを検索
    search = "SELECT * FROM userInfo WHERE email = :email"
    result = await database.fetch_one(query=search, values={"email": input_email})

    # 検索結果をログに出力
    logging.info(f"検索結果: {result}")
    logging.info(f"ログイン試行: {input_email}")

    if result and input_pw == result['pw']:
        user_id = result['userId']
        if user_id is None:
            logging.error("userIdがresultに存在しません。")
            raise HTTPException(status_code=400, detail="ログイン処理中にエラーが発生しました。")

        # ログイン日時を更新
        await database.execute("UPDATE userInfo SET loginDate = NOW() WHERE userId = :userId", values={"userId": user_id})

        request.session['userId'] = user_id
        return {"message": "ログイン成功！", "data": result}
    else:
        return {"message": "メールやパスワードをもう一度ご確認お願いします。"}


@app.post("/register")
async def register(data: registerData):
    try:
        # メールの重複チェック
        check_email_query = "SELECT * FROM userInfo WHERE email = :email"
        existing_user = await database.fetch_one(check_email_query, values={"email": data.email})

        if existing_user:
            return {"message": "同じメイルです。", "status_code": 400}
        
        # 会員登録クエリ
        insert_query = """
        INSERT INTO userInfo (name, email, pw, gender, birthDate, phone, mkt)
        VALUES (:name, :email, :pw, :gender, :birthDate, :phone, :mkt)
        """
        
        values = {
            "name": data.name,
            "email": data.email,
            "pw": data.pw,  # 実際にはパスワードをハッシュ化して保存する必要があります（例：bcryptを使用）
            "gender": data.gender,
            "birthDate": data.birthDate,
            "phone": data.phone,
            "mkt": data.mkt
        }
        
        await database.execute(insert_query, values=values)
        
        # 会員登録成功時にログインページに移動
        return {"message": "会員登録成功", "status_code": 200}

    except ValidationError as e:
        # データ検証失敗時の処理
        return {"message": "会員登録失敗: データタイプが正しくありません。", "status_code": 400}
        # raise HTTPException(status_code=400, detail="会員登録に失敗しました: データ形式が正しくありません。")

    except Exception as e:
        # ネットワークの問題またはその他のエラー処理
        return {"message": "登録中に問題が発生しました。", "status_code": 500}
        # raise HTTPException(status_code=500, detail="会員登録中に問題が発生しました。")


# パスワード変更 API を作成
@app.put("/changePw")
async def change_password(data: changeData):
    try:
        # メールアドレスと現在のパスワードが一致するか確認
        check_pw_query = "SELECT * FROM userInfo WHERE email = :email AND pw = :current_pw"
        current_user = await database.fetch_one(query=check_pw_query, values={"email": data.email, "current_pw": data.current_pw})

        if not current_user:
            return {"message": "パスワードが一致しません。", "status_code": 400}

        # 新しいパスワードで更新
        update_pw_query = "UPDATE userInfo SET pw = :new_pw WHERE email = :email"
        await database.execute(query=update_pw_query, values={"new_pw": data.new_pw, "email": data.email})
        return {"message": "パスワード変更成功", "status_code": 200}

    except ValidationError as e:
        return {"message": "パスワード変更成功: データタイプが正しくありません。", "status_code": 400}

    except Exception as e:
        return {"message": "パスワードの変更中に問題が発生しました。", "status_code": 500}
        
        
@app.post("/stt")
async def transcribe_audio(request: Request, file: UploadFile = File(...)):
    logging.info("音声認識プロセスが開始されました。")
    
    try:
        audio_content = await file.read()
        logging.info(f"受信したファイル: {file.filename}, サイズ: {len(audio_content)} バイト")

        # WEBM 形式の場合は WAV に変換
        if file.content_type == "audio/webm":
            logging.info("WEBM 形式を WAV に変換中です。")
            audio_segment = AudioSegment.from_file(io.BytesIO(audio_content), format="webm")
            audio_segment = audio_segment.set_sample_width(2)  # 16ビット設定
            output_io = io.BytesIO()
            audio_segment.export(output_io, format="wav")
            audio_content = output_io.getvalue()
            logging.info("WEBM オーディオが WAV に変換されました。")

        # 音声認識
        audio = speech.RecognitionAudio(content=audio_content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=48000,
            language_code="ja-JP",
        )

        response = stt_client.recognize(config=config, audio=audio)

        if response.results:
            transcript = response.results[0].alternatives[0].transcript
            logging.info("音声認識成功。")

            user_id = request.session.get("userId")
            logging.info(f"Retrieved userId: {user_id}")

            if user_id is None:
                logging.error("userIdがセッションに存在しません。")
                raise HTTPException(status_code=400, detail="ユーザーIDが必要です。")

            # TextTableにデータを挿入後 textId を返す
            insert_query = "INSERT INTO TextTable (userId, A) VALUES (:user_id, :transcript)" 
            await database.execute(insert_query, {"user_id": user_id, "transcript": transcript})
            text_id_query = "SELECT textId FROM TextTable WHERE userId=:user_id ORDER BY textId DESC LIMIT 1"
            textId = await database.fetch_one(text_id_query, {"user_id": user_id})

            # textId をセッションに保存
            if textId:
                request.session['textId'] = textId['textId']  # 辞書から 'textId' キーにアクセス

            # textId をログに出力
            logging.info(f"保存された textId: {textId['textId']}")  # 辞書から 'textId' キーにアクセス

            return {"transcript": transcript, "textId": textId["textId"], "message": "データが保存されました。"}
        else:
            logging.info("認識された結果がありません。")
            return {"transcript": "データの認識結果がありません。"}

    except Exception as e:
        logging.error(f"例外発生: {str(e)}")
        raise HTTPException(status_code=500, detail=f"サーバー内部エラー: {str(e)}")


# 例外処理機能を追加
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logging.error(f"検証エラー: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

@app.post("/GPT") ########### よく動くので絶対に触らないでください
async def categorize_response(data: TextRequest):
    logging.info(f"受信したデータ: {data}")
    try:
        # OpenAI APIを呼び出す
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": f"「海岸ビュー、シティビュー、レトロ」のようにカフェの雰囲気を感じる場合は「スタイル」であり、「デザート、音楽、コーヒーの味」のようにカフェのサービスを感じる場合は「サービス」に分類してください。必ず「スタイル」または「サービス」の一つの結果が出るようにしてください。二つの結果が出てはいけません。{data.transcript}"}]
        )

        analysis_result = response['choices'][0]['message']['content'].strip()
        logging.info(f"分析結果: {analysis_result}")

        # 分析結果の検証と aId の取得
        classification = None
        if "サービス" in analysis_result:
            classification = "サービス"
        elif "スタイル" in analysis_result:
            classification = "スタイル"

        if classification is None:
            logging.error(f"予期しない分類結果: {analysis_result}")
            raise HTTPException(status_code=400, detail=f"無効な分類: {analysis_result}. 'サービス'または'スタイル'である必要があります。")

        # スタイル aId の取得
        #style_query = "SELECT aId FROM AItem WHERE content = 'スタイル'"
        #style_aId = await database.fetch_one(style_query)

        #if not style_aId:
            #raise HTTPException(status_code=404, detail="スタイルに該当するaIdが見つかりません。")

        # bitems を取得
        query_bitem = "SELECT bId, content FROM BItem WHERE aId = :aId"
        if classification == "スタイル":
            # スタイルの場合はサービスの bitems を取得
            style_aId = 1  # サービスの aId
            bitem_values = {"aId": style_aId}
        else:
            # サービスの場合はスタイルの bitems を取得
            style_aId = 2  # スタイルの aId
            bitem_values = {"aId": style_aId}

        bitem_contents = await database.fetch_all(query_bitem, values=bitem_values)

        # 結果をリスト形式に変換
        bitems = [{"bId": item["bId"], "content": item["content"]} for item in bitem_contents]

        return {
            "message": "データが成功裏に保存されました。",
            "analysis_result": classification,
            "aId": style_aId,
            "bitems": bitems  # bId と content のリスト
        }

    except openai.error.OpenAIError as e:
        logging.error(f"OpenAI API 呼び出し中にエラーが発生しました: {str(e)}")
        raise HTTPException(status_code=500, detail="OpenAI APIエラーが発生しました")

    except Exception as e:
        logging.error(f"例外が発生しました: {str(e)}")
        raise HTTPException(status_code=500, detail="サーバー内部エラー: {str(e)}")


@app.post("/btext/{bitem}") 
async def save_btext(request: Request, bitem: str):
    # logging.info(f"Received BText data: {data}")
    logging.info(bitem)
    # セッションから userId と textId を取得
    user_id = request.session.get("userId")
    # text_id = request.session.get("textId")

    if user_id is None:
        logging.error("userIdがセッションに存在しません。")
        raise HTTPException(status_code=400, detail="ユーザーIDが必要です。")

    # if text_id is None:
    #     logging.error("textIdがセッションに存在しません。")
    #     raise HTTPException(status_code=400, detail="textIdが必要です。")

    try:
        # BItem から bId に該当する内容を取得
        # bitem_query = "SELECT content FROM BItem WHERE bId = :bId"
        # bitem = await database.fetch_one(bitem_query, values={"bId": data.bId})

        # if not bitem:
        #     raise HTTPException(status_code=404, detail="BItemが存在しません。")
        
        # user_id = request.session.get("userId")
        text_id_query = "SELECT textId FROM TextTable WHERE userId=:user_id ORDER BY textId DESC LIMIT 1"
        textId_record = await database.fetch_one(text_id_query, {"user_id": user_id})
        textId = textId_record['textId']  # Record オブジェクトから textId を抽出
        
        logging.info(textId)
        # TextTable の B フィールドを更新
        update_query = """
        UPDATE TextTable
        SET B = :bContent
        WHERE textId = :textId
        """
        await database.execute(update_query, {
            "textId": textId,  # セッションから取得した textId を使用
            "bContent": bitem  # 選択した BItem の内容を保存
        })

        return {"message": "Bフィールドが正常に更新されました。"}

    except Exception as e:
        logging.error(f"例外が発生しました: {str(e)}")
        raise HTTPException(status_code=500, detail="サーバー内部エラーが発生しました。")


async def extract_exactly_8_keywords(ctext: str):  # -> List[str]:
    """
    Ctext から正確に8つのキーワードを抽出します。
    GPTを使用してキーワードが8個未満の場合、追加のキーワードを生成して8個にします。
    """
    keyword_prompt = f"""次の文章から、正確に8つのキーワードを抽出してください。\n\n文章: {ctext}
                        JSON形式で出力してください。例: {{ "keyword1": "keyword1", "keyword2": "keyword2", "keyword3": "keyword3", "keyword4": "keyword4", "keyword5": "keyword5", "keyword6": "keyword6", "keyword7": "keyword7", "keyword8": "keyword8"}}"""

    keyword_response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": keyword_prompt}]
    )

    keywords = keyword_response['choices'][0]['message']['content'].strip()
    logging.info(f"GPT Response: {keywords}")

    # JSON パースを試みる
    try:
        # JSON 文字列の最初の中括弧を見つけてその後の文字列を切り取る
        json_str = keywords[keywords.index('{'):] if '{' in keywords else keywords
        keywords_dict = json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as e:
        logging.error(f"JSON パースエラー: {str(e)}. 応答内容: {keywords}")
        return [""] * 8  # エラーが発生した場合は空の文字列を8つ返す

    # 空のキーワードやスペースのみのキーワードを除去
    keywords_list = [keyword.strip() for keyword in keywords_dict.values() if keyword.strip()]

    # 8個にならない場合、追加のキーワードを生成するリクエスト
    while len(keywords_list) < 8:
        additional_prompt = f"現在のキーワード: {', '.join(keywords_list)}. さらにキーワードを生成して、合計で8つになるようにしてください。"
        additional_response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": additional_prompt}]
        )
        additional_keywords = additional_response['choices'][0]['message']['content'].strip()

        # 追加キーワード JSON パース
        try:
            additional_keywords_dict = json.loads(additional_keywords)
            keywords_list += [v for v in additional_keywords_dict.values() if v.strip()]
        except json.JSONDecodeError as e:
            logging.error(f"追加キーワード JSON パースエラー: {str(e)}")
            break  # それ以上追加しない

    # 最終的に正確に8個にする
    while len(keywords_list) < 8:
        keywords_list.append("")

    return keywords_list[:8]


@app.post("/ctext")
async def create_ctext_and_extract_keywords(request: Request):
    try:
        # userIdを使用してTextTableからAとBフィールドを取得
        user_id = request.session.get("userId")
        abtext_query = "SELECT A, B FROM TextTable WHERE userId = :userId ORDER BY textId DESC LIMIT 1"
        abtext_record = await database.fetch_one(query=abtext_query, values={"userId": user_id})
        
        if not abtext_record:
            raise HTTPException(status_code=404, detail="該当するuserIdに対するAtextとBtextがありません。")
        
        atext = abtext_record['A']
        btext = abtext_record['B']

        # A, B テキストが空でないか確認
        if not atext or not btext:
            raise HTTPException(status_code=400, detail="AtextまたはBtextが空です。")
        
        logging.info(f"Atext: {atext}, Btext: {btext}")

        # OpenAI GPTを使用してAtextとBtextを結合したCtextを生成
        prompt = f"AtextとBtextを使って、一文にまとめてください。\n\nAtext: {atext}\n\nBtext: {btext}"
        try:
            ctext_response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}]
            )
        except openai.error.OpenAIError as e:
            logging.error(f"OpenAI API呼び出し中にエラーが発生しました: {str(e)}")
            raise HTTPException(status_code=500, detail=f"OpenAI API エラー: {str(e)}")
        
        # 生成されたCtextを確認
        logging.info(f"OpenAIの応答: {ctext_response}")
        ctext = ctext_response['choices'][0]['message']['content'].strip()
        if not ctext:
            raise HTTPException(status_code=500, detail="Ctextの生成に失敗しました。")
        logging.info(f"生成されたCtext: {ctext}")

        # CtextをTextTableのCフィールドに更新 (userIdを使用して更新)
        update_ctext_query = "UPDATE TextTable SET C = :ctext WHERE userId = :userId ORDER BY textId DESC LIMIT 1"
        await database.execute(update_ctext_query, {"ctext": ctext, "userId": user_id})

        # Ctextを使用して正確に8つのキーワードを抽出
        keywords = await extract_exactly_8_keywords(ctext)

        # 現在の時間を取得 (keyword8が保存されるときの時間)
        keyword_date = datetime.now()
        logging.info(f"キーワードの日付と時間: {keyword_date}")

        # キーワードが正確に8個であることを最終確認
        # if len(keywords) < 8:
        #     raise ValueError("キーワードの個数が8個ではありません。 エラー発生")

        # キーワードをそれぞれフィールドに合わせて保存
        keyword_insert_query = """
        INSERT INTO Keyword (userId, keyword1, keyword2, keyword3, keyword4, keyword5, keyword6, keyword7, keyword8, keywordDate)
        VALUES (:userId, :keyword1, :keyword2, :keyword3, :keyword4, :keyword5, :keyword6, :keyword7, :keyword8, :keywordDate)
        """
        logging.info(keywords[0])
        logging.info(keywords[1])
        logging.info(keywords[2])
        logging.info(keywords[3])
        logging.info(keywords[4])
        logging.info(keywords[5])
        logging.info(keywords[6])
        logging.info(keywords[7])
        await database.execute(keyword_insert_query, {
            "userId": user_id,
            "keyword1": keywords[0],
            "keyword2": keywords[1],
            "keyword3": keywords[2],
            "keyword4": keywords[3],
            "keyword5": keywords[4],
            "keyword6": keywords[5],
            "keyword7": keywords[6],
            "keyword8": keywords[7],
            "keywordDate": keyword_date
        })

        logging.info(f"Keywords successfully saved for userId {user_id}")
        # 過去のもの (現在は使用していません)
        # return {
        #     "message": "Ctextとキーワードが正常に作成·保存されました。",
        #     # "message": "Ctextおよびキーワードが正常に生成され、保存されました。",
        #     "Atext": atext,
        #     "Btext": btext,
        #     "Ctext": ctext,
        #     "Keywords": keywords,
        #     "keywordDate": keyword_date.strftime("%Y-%m-%d %H:%M:%S")
        # }
    
        return {
            "Ctext": ctext
        }    
    
        # return {"message": "Ctextの生成および更新に成功しました", "Atext": atext, "Btext": btext, "Ctext": ctext}

    except openai.error.OpenAIError as e:
        logging.error(f"OpenAI API呼び出し中にエラーが発生しました: {str(e)}")
        raise HTTPException(status_code=500, detail="OpenAI APIエラーが発生しました。")
        # raise HTTPException(status_code=500, detail="OpenAI APIエラーが発生しました。")

    except Exception as e:
        logging.error(f"例外が発生しました: {str(e)}")
        raise HTTPException(status_code=500, detail=f"サーバー内部エラー: {str(e)}")
        # raise HTTPException(status_code=500, detail=f"サーバー内部エラー: {str(e)}")

    except Exception as e:
        logging.error(f"例外が発生しました: {str(e)}")
        raise HTTPException(status_code=500, detail=f"サーバー内部エラー: {str(e)}")


@app.post("/image")
async def generate_image(request: ImageRequest, request_obj: Request):
    try:
        # セッションからユーザーIDを取得
        user_id = request_obj.session.get("userId")
        if user_id is None:
            raise HTTPException(status_code=400, detail="ユーザーがログインしていません。")

        # トラッキングのために元のプロンプトをログに記録
        original_prompt = request.prompt
        logging.info(f"Original Prompt: {original_prompt}")

        # 前提条件を追加
        refined_prompt = f"日本の神戸にあるカフェを前提にして {original_prompt}"

        logging.info(f"Refined Prompt: {refined_prompt}")

        # OpenAI APIに洗練されたプロンプトを送信
        response = openai.Image.create(
            model="dall-e-3",
            prompt=refined_prompt,  # 前提条件付きの洗練されたプロンプトを使用
            n=1,
            size="1024x1024"
        )

        # 生成された画像のURLを抽出
        image_url = response['data'][0]['url']
        logging.info(f"Generated Image URL: {image_url}")

        # データベースに画像のURLを保存
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        query = """
            INSERT INTO Media (userId, image, createDate)
            VALUES (:userId, :image, :createDate)
        """
        values = {
            "userId": user_id,  # セッションからユーザーIDを取得
            "image": image_url,
            "createDate": current_time
        }

        await database.execute(query=query, values=values)
        return {"image_url": image_url}

    except Exception as e:
        logging.error(f"画像生成またはデータベース挿入中のエラー: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Image生成に失敗しました。: {str(e)}")


@app.post("/cafeList")
async def create_cafe_list(request: Request):
    try:
        # ステップ1: セッションからuserIdを取得します。
        user_id = request.session.get("userId")
        if user_id is None:
            raise HTTPException(status_code=400, detail="ユーザーIDがセッションにありません。")
        
        # ステップ2: ユーザーの最新8つのキーワードを取得します。
        keyword_query = """
        SELECT keyword1, keyword2, keyword3, keyword4, keyword5, keyword6, keyword7, keyword8
        FROM Keyword WHERE userId = :userId ORDER BY keywordDate DESC LIMIT 1
        """
        keyword_record = await database.fetch_one(keyword_query, values={"userId": user_id})
        
        if not keyword_record:
            raise HTTPException(status_code=404, detail="該当ユーザーIDのキーワードがありません。")
        
        # ステップ3: 8つのキーワードに基づいてカフェの名前を生成します。
        keywords = ", ".join([keyword_record[f"keyword{i}"] for i in range(1, 9)])
        prompt = f"""あなたは神戸のカフェを専門にする案内人です。以下のキーワードを活用して、神戸に位置する3つのカフェの名前を教えてください。 
ただし、必ず次のようなJSON形式で答えてください：
{{ "カフェ名前1": "カフェ名1", "カフェ名前2": "カフェ名2", "カフェ名前3": "カフェ名3" }}

キーワード: {keywords}
例外的な内容（例：「検索できません」、「適用できません」などの表現）は不要です。
もしキーワードに関する情報が不足している場合は、カフェの雰囲気やサービスを考慮し、適切なカフェ名を提供してください。"""

        # ステップ4: GPT-4にリクエストしてカフェの名前を取得します。
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )

        # 応答のロギング
        logging.info(f"OpenAIの応答: {response}")

        # ステップ5: GPTの応答からカフェの名前を抽出します。
        cafe_info = response['choices'][0]['message']['content'].strip()
        logging.info(f"受け取ったカフェ情報: {cafe_info}")

        # JSON文字列が正しくパースできるか確認
        try:
            # JSONパースを試みる
            cafes = json.loads(cafe_info)
            logging.info(f"パースされたJSONオブジェクト: {cafes}")
        except json.JSONDecodeError as e:
            logging.error(f"JSONパースエラー: {str(e)}. 応答内容: {cafe_info}")
            raise HTTPException(status_code=400, detail="無効なJSON形式の応答です。")

        # JSONがパースされた後、各カフェの名前を安全に取得
        cafe_name1 = cafes.get("カフェ名前1", "名前を取得できませんでした。")
        cafe_name2 = cafes.get("カフェ名前2", "名前を取得できませんでした。")
        cafe_name3 = cafes.get("カフェ名前3", "名前を取得できませんでした。")

        # Google検索URLの生成
        url1 = f"https://www.google.com/search?q={cafe_name1}+神戸"
        url2 = f"https://www.google.com/search?q={cafe_name2}+神戸"
        url3 = f"https://www.google.com/search?q={cafe_name3}+神戸"

        # カフェの名前とURLをCafeListテーブルに保存します。
        insert_query = """
        INSERT INTO CafeList (userId, cafeName1, cafeName2, cafeName3, URL1, URL2, URL3)
        VALUES (:userId, :cafeName1, :cafeName2, :cafeName3, :URL1, :URL2, :URL3)
        """
        values = {
            "userId": user_id,
            "cafeName1": cafe_name1,
            "cafeName2": cafe_name2,
            "cafeName3": cafe_name3,
            "URL1": url1,
            "URL2": url2,
            "URL3": url3
        }

        await database.execute(insert_query, values=values)

        return {"message": "カフェ情報が正常に保存されました。", "cafes": cafes, "urls": [url1, url2, url3]}

    except HTTPException as http_exception:
        logging.error(f"HTTP例外が発生しました: {http_exception.detail}")
        raise
    except Exception as e:
        logging.error(f"サーバーエラーが発生しました: {str(e)}")
        raise HTTPException(status_code=500, detail="サーバー内部エラーが発生しました。")


@app.post("/getCafeList")
async def get_cafe_list(request: Request):
    try:
        # セッションからuserIdを取得
        user_id = request.session.get("userId")
        if user_id is None:
            raise HTTPException(status_code=400, detail="ユーザーIDがセッションにありません。")

        # CafeListテーブルから該当するuserIdのすべてのカフェ情報を取得
        query = """
        SELECT cafeId, cafeName1, cafeName2, cafeName3, URL1, URL2, URL3
        FROM CafeList 
        WHERE userId = :userId
        """
        cafe_records = await database.fetch_all(query, values={"userId": user_id})

        if not cafe_records:
            raise HTTPException(status_code=404, detail="該当するユーザーのカフェ情報がありません。")

        # カフェ情報をリストに変換（URL含む）
        cafes = [{
            "cafeId": record["cafeId"], 
            "cafeName1": record["cafeName1"], "URL1": record["URL1"],
            "cafeName2": record["cafeName2"], "URL2": record["URL2"],
            "cafeName3": record["cafeName3"], "URL3": record["URL3"]
        } for record in cafe_records]

        logging.info(cafes)
        return {"cafes": cafes}

    except HTTPException as http_exception:
        logging.error(f"HTTP例外が発生しました: {http_exception.detail}")
        raise
    except Exception as e:
        logging.error(f"サーバーエラーが発生しました: {str(e)}")
        raise HTTPException(status_code=500, detail="サーバー内部エラーが発生")
               
