
import streamlit as st
import requests
import re
import pyodbc
from bs4 import BeautifulSoup

# ===================== DB SETUP =====================
def get_azure_connection():
    secrets = st.secrets["azure_db"]
    conn_str = (
        f"DRIVER={{{secrets['driver']}}};"
        f"SERVER={secrets['server']};"
        f"DATABASE={secrets['database']};"
        f"UID={secrets['user']};"
        f"PWD={secrets['password']}"
    )
    return pyodbc.connect(conn_str)

def create_tables(conn):
    c = conn.cursor()
    c.execute('''
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='recipes' and xtype='U')
        CREATE TABLE recipes (
            id INT IDENTITY(1,1) PRIMARY KEY,
            title NVARCHAR(MAX)
        )
    ''')
    c.execute('''
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='ingredients' and xtype='U')
        CREATE TABLE ingredients (
            id INT IDENTITY(1,1) PRIMARY KEY,
            recipe_id INT,
            ingredient NVARCHAR(MAX)
        )
    ''')
    c.execute('''
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='instructions' and xtype='U')
        CREATE TABLE instructions (
            id INT IDENTITY(1,1) PRIMARY KEY,
            recipe_id INT,
            step_number INT,
            instruction NVARCHAR(MAX)
        )
    ''')
    c.execute('''
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='macros' and xtype='U')
        CREATE TABLE macros (
            id INT IDENTITY(1,1) PRIMARY KEY,
            recipe_id INT,
            name NVARCHAR(100),
            value FLOAT
        )
    ''')
    conn.commit()

# ===================== RECIPE PARSING =====================
def fetch_text(source: str, is_file=False):
    if is_file:
        return source.read().decode("utf-8")
    elif source.startswith("http"):
        response = requests.get(source)
        soup = BeautifulSoup(response.content, "lxml")
        return soup.get_text(separator="\n")
    else:
        return source

def split_recipes(text):
    return re.split(r"(?:^|\n)(?:recipe\s?:|===+|---+)", text, flags=re.IGNORECASE)

def extract_ingredients(text):
    lines = text.splitlines()
    clean_ingredients = []

    for line in lines:
        line = line.strip()
        # Accept if it looks like a real ingredient measurement
        if re.match(r"^[-*•]?\s*\d+(\.\d+)?\s?(cup|tsp|tbsp|g|gram|oz|ml|kg|lb|teaspoon|tablespoon|clove|slice|scoop|packet|can|stick)\b", line, re.IGNORECASE):
            clean_ingredients.append(line)
        # Accept bullet points or lines that include food + amount
        elif re.match(r"^[-*•]?\s*\d+\s.*", line) and any(unit in line.lower() for unit in ["cup", "tsp", "tbsp", "oz", "g", "ml", "kg", "lb"]):
            clean_ingredients.append(line)

    return clean_ingredients

def extract_instructions(text):
    lines = text.splitlines()
    instructions = []
    found = False

    for line in lines:
        line = line.strip()

        # Start capturing after instruction headers
        if not found and any(h in line.lower() for h in ["instructions", "directions", "method"]):
            found = True
            continue

        if found:
            # Stop capturing at "macros", "nutrition", "course", etc.
            if any(end in line.lower() for end in ["macros", "nutrition", "course", "calories", "psst"]):
                break
            if line and not line.lower().startswith("tag us") and len(line.split()) > 2:
                instructions.append(line)

    return instructions


def extract_macros(text):
    macros = {}
    pattern = re.compile(r"(calories|protein|fat|carbs|carbohydrates|fiber|sugar|cholesterol|sodium)[^\d]*(\d+\.?\d*)", re.I)
    for match in pattern.finditer(text):
        name = match.group(1).lower()
        value = float(match.group(2))
        macros[name] = value
    return macros

def save_recipe(conn, title, ingredients, instructions, macros):
    c = conn.cursor()
    c.execute("INSERT INTO recipes (title) VALUES (?)", (title,))
    c.execute("SELECT @@IDENTITY")
    recipe_id = int(c.fetchone()[0])

    for ing in ingredients:
        c.execute("INSERT INTO ingredients (recipe_id, ingredient) VALUES (?, ?)", (recipe_id, ing))

    for idx, instr in enumerate(instructions, 1):
        c.execute("INSERT INTO instructions (recipe_id, step_number, instruction) VALUES (?, ?, ?)", (recipe_id, idx, instr))

    for name, value in macros.items():
        c.execute("INSERT INTO macros (recipe_id, name, value) VALUES (?, ?, ?)", (recipe_id, name, value))

    conn.commit()

def extract_and_store_all_recipes(source_text, conn, is_file=False):
    raw_text = fetch_text(source_text, is_file)
    blocks = split_recipes(raw_text)

    for block in blocks:
        if len(block.strip()) < 20:
            continue
        title_match = re.search(r"(recipe\s*[:\-])?\s*([A-Za-z ,]+)", block.strip(), re.IGNORECASE)
        title = title_match.group(2).strip() if title_match else "Untitled Recipe"

        ingredients = extract_ingredients(block)
        instructions = extract_instructions(block)
        macros = extract_macros(block)

        if ingredients or instructions:
            save_recipe(conn, title, ingredients, instructions, macros)

# ===================== STREAMLIT APP =====================
def main():
    st.set_page_config(page_title="📋 Recipe Parser", layout="wide")
    st.title("📋 Recipe Parser & Viewer")

    conn = get_azure_connection()
    create_tables(conn)

    st.sidebar.header("➕ Add a Recipe")
    option = st.sidebar.radio("Input type", ("Paste Link", "Upload File"))

    if option == "Paste Link":
        url = st.sidebar.text_input("Enter a recipe page URL")
        if st.sidebar.button("Process URL"):
            extract_and_store_all_recipes(url, conn)
            st.sidebar.success("Recipes extracted and saved!")

    elif option == "Upload File":
        uploaded_file = st.sidebar.file_uploader("Upload text file with recipes", type=["txt"])
        if uploaded_file and st.sidebar.button("Process File"):
            extract_and_store_all_recipes(uploaded_file, conn, is_file=True)
            st.sidebar.success("Recipes extracted and saved!")

    st.subheader("📚 Stored Recipes")
    recipes = conn.cursor().execute("SELECT id, title FROM recipes").fetchall()
    for recipe_id, title in recipes:
        with st.expander(f"🍽️ {title}"):
            c = conn.cursor()
            ingredients = c.execute("SELECT ingredient FROM ingredients WHERE recipe_id = ?", (recipe_id,)).fetchall()
            instructions = c.execute("SELECT step_number, instruction FROM instructions WHERE recipe_id = ?", (recipe_id,)).fetchall()
            macros = c.execute("SELECT name, value FROM macros WHERE recipe_id = ?", (recipe_id,)).fetchall()

            st.markdown("**🧂 Ingredients:**")
            for ing in ingredients:
                st.write(f"- {ing[0]}")

            st.markdown("**👨‍🍳 Instructions:**")
            for step_num, instr in instructions:
                st.write(f"{step_num}. {instr}")

            if macros:
                st.markdown("**📊 Macros:**")
                for name, value in macros:
                    st.write(f"{name.title()}: {value}")

    conn.close()

if __name__ == "__main__":
    main()
