from tkinter import Image
import sys
import os

# 将项目根目录加入系统路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests
from flask import Flask, jsonify, render_template, request, current_app
from py2neo import Graph
import jieba
from wordcloud import WordCloud
import base64
import io
import os
import pandas as pd
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import hashlib
from io import BytesIO
from dotenv import load_dotenv
from dashscope import ImageSynthesis
from enum import Enum
from flask_cors import CORS


class DiffusionModel(str, Enum):
    STABLE_DIFFUSION_XL = "stable-diffusion-xl"
    ERNIE_ViLG = "ernie-vilg"


# 统一清洗传入的name
# 使用绝对导入
from data_processing.neo4j_import import clean_author_name

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)  # 确保能加载上级目录的.env
else:
    raise RuntimeError("未找到.env文件，请在项目根目录创建！")


def get_dashscope_client():
    return ImageSynthesis(api_key=os.getenv("DASHSCOPE_API_KEY"))


# 关键配置（使用相对路径）
app = Flask(__name__,
            static_folder='static',
            template_folder='templates')
CORS(app)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "ty123456")
graph = Graph(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

app.config['STATIC_FOLDER'] = os.path.abspath('web_app/static')

@app.route('/<path:path>')
def catch_all(path):
    # 对于所有不匹配的路径，返回 index.html
    return app.send_static_file('index.html') if path.startswith('static/') else render_template('index.html')

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/generate_ink', methods=['POST'])
def generate_ink_background():
    data = request.get_json()
    if not data or 'content' not in data:
        return jsonify({'error': '缺少诗文内容'}), 400

    poem_content = data['content'][:200]
    cache_key = hashlib.md5(poem_content.encode()).hexdigest()
    bg_path = os.path.join(app.config['STATIC_FOLDER'], 'images', 'poem_bg', f'{cache_key}.jpg')

    if os.path.exists(bg_path):
        return jsonify({'url': f'/static/images/poem_bg/{cache_key}.jpg'})

    try:
        # 优化后的Prompt模板
        prompt = f"""
        传统水墨风格插画，基于诗句：{poem_content}
        元素要求：
        - 使用水墨笔触技法，保留适当飞白效果
        - 布局采用散点透视构图
        - 色调为彩色+不要只有黑白
        - 全部为景物，不要出现文字
        - 用色建议：仅保留10%以下朱砂/花青点缀
        - 构图参考：南宋马远“边角之景”布局
        """

        # 调用阿里云SDK（同步示例）
        response = ImageSynthesis.call(
            model="wanx2.1-t2i-turbo",
            prompt=prompt,
            parameters={
                "size": "780*480",  # 匹配前端尺寸
                "n": 1,
                "style": "traditional ink painting",
                "composition_ratio": "3:2"  # 固定宽高比
            }
        )

        if response.status_code != 200:
            raise Exception(f"API错误：{response.message}")

        # 获取生成结果
        result = response.output.results[0]
        img_url = result.url

        # 图片后期处理
        img_resp = requests.get(img_url)
        img = Image.open(BytesIO(img_resp.content))

        # 增加水墨质感
        img = img.convert("L")  # 转灰度
        img = ImageEnhance.Contrast(img).enhance(1.2)  # 提高对比度
        img = ImageEnhance.Sharpness(img).enhance(1.1)  # 锐化笔触

        # 保存时压缩质量优化
        buffer = BytesIO()
        img.save(buffer, format="JPEG",
                 quality=85,
                 optimize=True,
                 progressive=True)
        buffer.seek(0)

        # 存储到本地
        with open(bg_path, 'wb') as f:
            f.write(buffer.getvalue())

        return jsonify({'url': f'/static/images/poem_bg/{cache_key}.jpg'})

    except Exception as e:
        current_app.logger.error(f"水墨生成失败 | 内容：{poem_content} | 错误：{str(e)}")
        return jsonify({
            'error': '生成服务繁忙',
            'fallback': '/static/images/default_ink_bg.jpg'
        }), 503


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/poets')
def get_poets():
    query = """
    MATCH (p:Poet) 
    RETURN p.name as name, p.num_poems as count 
    ORDER BY p.name
    LIMIT 100
    """
    return jsonify([dict(row) for row in graph.run(query)])


@app.route('/api/poet/<name>')
def get_poet(name):
    clean_name = clean_author_name(name)  # 前端传入的任何名称都统一清洗
    if not clean_name:
        return jsonify({'error': '诗人姓名无效'}), 400

    try:
        # 基本信息
        poet_data = graph.run("""
                    MATCH (p:Poet {name: $name})
                    RETURN p.name as name, p.birth as birth, p.death as death,
                           p.bio as bio, p.num_poems as poem_count
                    """, {'name': clean_name}).data()[0]

        # 获取本地简介数据
        intro_df = pd.read_excel(os.path.join(current_app.root_path, '../data/introduction.xlsx'))
        bio_row = intro_df[intro_df['author'].apply(clean_author_name) == clean_name]
        poet_data['bio'] = bio_row['produce'].values[0] if (
                    not bio_row.empty and len(bio_row['produce'].values) > 0) else "暂无简介"

        # 迁徙路线
        locations = graph.run("""
            MATCH (p:Poet)-[r:VISITED]->(pl:Place)
            WHERE p.name = $name 
            RETURN pl.name as name, pl.lat as lat, pl.lon as lon,
                   r.year as year, r.event as event
            ORDER BY r.year
            """, {'name': name}).data()

        # 社交网络
        relations = graph.run("""
                   MATCH (p:Poet {name: $name})-[:FRIEND_OF]-(friend:Poet)
                   RETURN DISTINCT friend.name as name
                   ORDER BY friend.name
               """, {'name': clean_name}).data()

        # 代表作品
        poems = graph.run("""
            MATCH (p:Poet {name: $name})-[:WROTE]->(poem:Poem)
            RETURN poem.title as title,
                   poem.content as content,
                   poem.trans_content as trans_content,
                   poem.appear as appear,
                   poem.background as background,
                   poem.tags as tags,  
                   poem.formal as formal,
                   poem.data as data,
                   poem.zhu as zhu
            LIMIT 5
        """, {'name': name}).data()

        # 详细简介
        bio_response = requests.get(f'http://localhost:5001/api/poet_bio/{name}')
        poet_data['bio'] = bio_response.json()['bio'] if bio_response.ok else "暂无简介"

        return jsonify({
            'info': poet_data,
            'locations': locations,
            'relations': [r['name'] for r in relations],
            'poems': poems[:1]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def load_stopwords(file_path):
    """
    加载停用词表
    :param file_path: 停用词表文件路径
    :return: 停用词集合
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        stopwords = set([line.strip() for line in f.readlines()])
    return stopwords


@app.route('/wordcloud/<name>')
def generate_wordcloud(name):
    try:
        # 动态构建字体路径（关键修正）
        font_dir = os.path.join(app.static_folder, 'fonts')
        font_path = os.path.join(font_dir, 'simhei.ttf')

        # 添加路径验证
        if not os.path.isfile(font_path):
            raise FileNotFoundError(f"字体文件未找到: {font_path}")

        # 生成词云（添加错误处理）
        poems = graph.run("""
            MATCH (p:Poet {name: $name})-[:WROTE]->(poem)
            RETURN poem.content as content
            """, {'name': name}).data()

        if not poems:
            raise ValueError("没有找到相关诗作")

        text = ''.join([p.get('content', '') for p in poems])
        wordlist = jieba.cut(text, cut_all=False)  # 这里使用精确模式进行分词

        # 加载停用词表
        root_path = os.path.abspath(os.path.dirname(__file__))
        stopwords_file = os.path.join(root_path, 'data', '哈工大停用词表.txt')
        stopwords = load_stopwords(stopwords_file)
        # 过滤无意义的词
        filtered_wordlist = [word for word in wordlist if word not in stopwords]

        # 根据诗人名字生成结果文件路径
        result_dir = os.path.join(os.path.dirname(__file__), 'poet_segmentation_results')
        if not os.path.exists(result_dir):
            os.makedirs(result_dir)
        result_file_path = os.path.join(result_dir, f'{name}_segmentation_result.txt')

        # 将分词结果保存到文件
        with open(result_file_path, 'w', encoding='utf-8') as result_file:
            result_file.write(' '.join(filtered_wordlist))

        # 将过滤后的分词结果转换为字符串
        filtered_word_str = ' '.join(filtered_wordlist)

        wc = WordCloud(
            font_path=font_path,  # 使用验证过的路径
            width=800,
            height=500,
            background_color='white',
            max_words=200,
            collocations=False,  # 防止重复词
        )
        # 手动生成并绘图（避免自动生成中的兼容问题）
        wc.generate(filtered_word_str)
        # 转换为 PIL 图像
        image = wc.to_image()

        img_io = io.BytesIO()
        image.save(img_io, 'PNG')
        img_io.seek(0)
        return base64.b64encode(img_io.getvalue()).decode()

    except Exception as e:
        print(f"生成词云失败: {str(e)}")
        img = Image.new('RGB', (800, 500), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        try:
            # 使用兼容性字体加载
            font = ImageFont.truetype("arial.ttf", 40)  # 确保系统有该字体或替换为实际路径
        except:
            font = ImageFont.load_default()
        draw.text((100, 200), "词云生成失败", fill=(0, 0, 0), font=font)

        img_io = io.BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        return base64.b64encode(img_io.getvalue()).decode()


@app.route('/api/poet_bio/<name>')
def get_poet_bio(name):
    try:
        # 动态获取数据路径（关键修正）
        data_dir = os.path.join(current_app.root_path, '..', 'data')
        file_path = os.path.join(data_dir, 'introduction.xlsx')

        # 添加路径验证
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"数据文件不存在：{file_path}")

        df = pd.read_excel(file_path)
        bio = df[df['author'] == name]['produce'].values[0]
        return jsonify({'bio': bio})
    except Exception as e:
        return jsonify({'error': str(e)}), 404


@app.route('/api/poet_network/<name>')
def get_poet_network(name):
    try:
        # 查询诗人社交关系
        query = """
               MATCH (p:Poet {name: $name})-[r:FRIEND_OF]-(other)
               WHERE other.name IS NOT NULL AND other.name <> $name
               RETURN p.name as source, 
                      other.name as target, 
                      type(r) as relationship
               """
        result = graph.run(query, {'name': name}).data()

        # 构造D3.js所需数据格式
        nodes = set()
        links = []
        for row in result:
            nodes.add(row['source'])
            nodes.add(row['target'])
            links.append({
                'source': row['source'],
                'target': row['target'],
                'type': row['relationship']
            })

        return jsonify({
            'nodes': [{'id': node, 'label': node} for node in nodes],
            'links': links
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search_poem', methods=['GET'])
def search_poem():
    try:
        title_keyword = request.args.get('title', '').strip()
        author = request.args.get('author', '').strip()

        query = """
            MATCH (poet:Poet {name: $author})-[:WROTE]->(poem:Poem)
            WHERE toLower(poem.title) CONTAINS toLower($title_keyword)
            RETURN poem.title as title,
                   poem.content as content,
                   poem.trans_content as trans_content,
                   poem.appear as appear,
                   poem.background as background,
                   poem.tags as tags,
                   poem.formal as formal,
                   poem.data as data,
                   poem.zhu as zhu
        """
        results = graph.run(query, {'author': author, 'title_keyword': title_keyword}).data()
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/poet_heatmap_data/<name>')  # 密度热力图
def get_heatmap_data(name):
    try:
        query = """
        MATCH (:Poet {name: $name})-[:WROTE]->(poem)
        MATCH (poet)-[v:VISITED]->(place)
        RETURN place.lat as lat, place.lon as lon, count(poem) as intensity
        """
        results = graph.run(query, {'name': name}).data()
        return jsonify([dict(r) for r in results])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/poet_annual_counts/<name>')
def get_annual_counts(name):
    try:
        query = """
        MATCH (p:Poet {name: $name})-[:YEARLY_OUTPUT]->(ac:AnnualCount)
        RETURN ac.year AS year, ac.count AS count
        ORDER BY ac.year
        """
        results = graph.run(query, {'name': name}).data()
        return jsonify([dict(r) for r in results])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search_poem_titles', methods=['GET'])
def search_poem_titles():
    try:
        title_keyword = request.args.get('title', '').strip()
        author = request.args.get('author', '').strip()

        query = """
            MATCH (poet:Poet {name: $author})-[:WROTE]->(poem:Poem)
            WHERE toLower(poem.title) CONTAINS toLower($title_keyword)
            RETURN poem.title as title
        """
        results = graph.run(query, {'author': author, 'title_keyword': title_keyword}).data()
        titles = [row['title'] for row in results]
        return jsonify(titles)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # 新增历史时期统计接口
    @app.route('/api/poet_anshi_periods/<name>')
    def get_anshi_periods(name):
        def calc_period(start, end):
            query = f"""
            MATCH (p:Poet {{name: '{name}'}})-[:YEARLY_OUTPUT]->(ac:AnnualCount)
            WHERE ac.year >= {start} AND ac.year < {end}
            RETURN sum(ac.count) as total
            """
            return graph.run(query).evaluate()

        return jsonify({
            "pre_anshi": calc_period(750, 755),  # 安史之乱前
            "mid_anshi": calc_period(755, 757),  # 动乱中期
            "post_anshi": calc_period(757, 763)  # 战后恢复期
        })

    # 新增官职时期对比接口
    @app.route('/api/poet_office_periods/<name>')
    def get_office_periods(name):
        # 预设诗人官职时期（需根据实际情况维护）
        periods = {
            '李白': [{'type': '在任', 'start': 742, 'end': 744}],
            '杜甫': [{'type': '在任', 'start': 755, 'end': 759}],
            '王维': [{'type': '在任', 'start': 720, 'end': 728}],
            '白居易': [{'type': '在任', 'start': 800, 'end': 842}],
        }

        results = []
        for p in periods.get(name, []):
            query = f"""
            MATCH (:`Poet` {{name: '{name}'}})-[:YEARLY_OUTPUT]->(ac) 
            WHERE ac.year >= {p['start']} AND ac.year < {p['end']} 
            RETURN sum(ac.count) as count
            """
            results.append({
                'period': f"{p['start']}-{p['end']}",
                'type': p['type'],
                'count': graph.run(query).evaluate()
            })

        # 补充在野时期数据
        query = f"""
        MATCH (:Poet {{name: '{name}'}})-[:YEARLY_OUTPUT]->(ac) 
        RETURN sum(ac.count) - {sum([x['count'] for x in results])}
        """
        results.append({
            'period': '其他年份',
            'type': '在野',
            'count': graph.run(query).evaluate()
        })

        return jsonify(results)

    # 在原路由下方添加

    @app.route('/api/poet_anshi_periods/<name>')
    def get_anshi_periods(name):
        """获取安史之乱时期总产量"""
        clean_name = clean_author_name(name)

        # 计算各阶段总产量
        def sum_period(start, end):
            query = """
            MATCH (p:Poet {name: $name})-[:YEARLY_OUTPUT]->(ac:AnnualCount)
            WHERE ac.year >= $start AND ac.year < $end
            RETURN sum(ac.count) as total
            """
            result = graph.run(query, {'name': clean_name, 'start': start, 'end': end}).data()
            return result[0]['total'] if result else 0

        return jsonify({
            "pre_anshi": sum_period(750, 755),
            "mid_anshi": sum_period(755, 757),
            "post_anshi": sum_period(757, 763)
        })

    @app.route('/api/poet_office_periods/<name>')
    def get_office_periods(name):
        """获取仕途时期总产量"""
        clean_name = clean_author_name(name)

        # 查询在任期间总作品量
        office_query = """
        MATCH (p:Poet {name: $name})-[:YEARLY_OUTPUT]->(ac)
        WHERE ac.year IN [742,755,806]  # 示例年份，需根据实际数据调整
        RETURN sum(ac.count) as office_total
        """
        office_total = graph.run(office_query, {'name': clean_name}).data()[0].get('office_total', 0)

        # 总作品量
        total_query = """
        MATCH (p:Poet {name: $name})-[:YEARLY_OUTPUT]->(ac)
        RETURN sum(ac.count) as total
        """
        total = graph.run(total_query, {'name': clean_name}).data()[0].get('total', 0)

        return jsonify({
            "office": office_total,
            "non_office": total - office_total
        })


# 安史之乱时期统计 API
@app.route('/api/poet_anshi_periods/<name>')
def get_anshi_periods(name):
    # 统一姓名处理
    clean_name = clean_author_name(name)

    # 各时期范围定义
    periods = [
        {'name': 'pre', 'start': 750, 'end': 755},  # 安史之乱前
        {'name': 'mid', 'start': 755, 'end': 757},  # 动乱中期
        {'name': 'post', 'start': 757, 'end': 763}  # 恢复期
    ]

    results = {}
    for p in periods:
        query = """
        MATCH (p:Poet {name: $name})-[:YEARLY_OUTPUT]->(ac:AnnualCount)
        WHERE ac.year >= $start AND ac.year <= $end
        RETURN sum(ac.count) as total
        """
        result = graph.run(query, {'name': clean_name, 'start': p['start'], 'end': p['end']}).data()
        results[p['name'] + '_anshi'] = result[0]['total'] if result else 0

    return jsonify(results)


# 仕途时期统计 API
@app.route('/api/poet_office_periods/<name>')
def get_office_periods(name):
    clean_name = clean_author_name(name)

    # 任职时期手册数据（需要维护）
    official_years = {
        '李白': [742, 743, 744],
        '杜甫': [755, 756, 757, 758, 759],
        '王维': [721, 722, 723, 724, 756],
        '白居易': [806, 807, 808, 809, 810]
    }

    # 在任期间作品数
    query_in_office = """
    MATCH (p:Poet {name: $name})-[:YEARLY_OUTPUT]->(ac)
    WHERE ac.year IN $years
    RETURN sum(ac.count) as total
    """
    office_total = graph.run(query_in_office,
                             {'name': clean_name, 'years': official_years.get(clean_name, [])}
                             ).data()[0]['total']

    # 总作品数
    query_total = """
    MATCH (p:Poet {name: $name})-[:YEARLY_OUTPUT]->(ac)
    RETURN sum(ac.count) as total
    """
    all_total = graph.run(query_total, {'name': clean_name}).data()[0]['total']

    return jsonify([
        {  # 转换为数组格式
            "period": "",
            "type": "在任",
            "count": office_total
        },
        {
            "period": "",
            "type": "在野",
            "count": all_total - office_total
        }
    ])


@app.route('/api/poem_imagery')
def get_poem_imagery():
    """获取意象分析数据"""
    poet = request.args.get('poet', '白居易')

    # Cypher查询
    query = """
    MATCH (p:Poet {name:$name})-[:WROTE]->(poem)-[:CONTAINS_IMAGE]->(img)
    RETURN img.name as image, count(*) as freq
    ORDER BY freq DESC
    LIMIT 50
    """
    result = graph.run(query, name=poet).data()
    return jsonify([dict(item) for item in result])


@app.route('/api/imagery_cloud')
def generate_cloud():
    poet = request.args.get('poet', 'all')
    query = """
        MATCH (:Poet {name:$name})-[:WROTE]->()
        -[:CONTAINS_IMAGE]->(i)
        RETURN i.name as name, count(*) as value 
        ORDER BY value DESC LIMIT 50
    """ if poet != 'all' else """
        MATCH (i:Image) 
        RETURN i.name as name, count(*) as value 
        ORDER BY value DESC LIMIT 100
    """
    data = graph.run(query, name=poet).data()
    return jsonify([{'name': d['name'], 'value': d['value']} for d in data])  # 频率放大


@app.route('/api/period_imagery/<name>')
def get_period_imagery(name):
    try:
        # 安全处理查询条件
        condition_map = {
            'year < 755': "poem.year < 755",
            'year >= 755': "poem.year >= 755"
        }
        raw_cond = request.args.get('cond', '')
        safe_cond = condition_map.get(raw_cond, "TRUE")

        # 参数化查询(关键修正)
        query = f"""
        MATCH (p:Poet {{name: $name}})-[:WROTE]->(poem)
        WHERE {safe_cond}
        MATCH (poem)-[:CONTAINS_IMAGE]->(img)
        RETURN img.name as name, count(*) as value
        ORDER BY value DESC
        LIMIT 40
        """

        # 打印调试查询语句
        print(f"[DEBUG] Executing query:\n{query}")

        results = graph.run(query, {'name': clean_author_name(name)}).data()

        # 数据有效性检查
        valid_data = []
        for r in results:
            if isinstance(r['name'], str) and isinstance(r['value'], int):
                valid_data.append({
                    'name': r['name'].strip(),
                    'value': r['value'] * 2  # 保持与之前相同的放大系数
                })

        return jsonify(valid_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# 确保这是文件最后一行 ⬇️ 必须添加！
if __name__ == '__main__' and os.getenv('ENV') != 'vercel':
    # 本地运行
    app.run(debug=True)
else:
    # Vercel 部署
    handler = app

