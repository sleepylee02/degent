import polars as pl

# Load movies.csv
movies = pl.read_csv('../../data/ml-32m/raw/movies.csv')

# Define all genres in the order they appear in the dataset
all_genres = ['(no genres listed)', 'Action', 'Adventure', 'Animation', 'Children', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Fantasy', 'Film-Noir', 'Horror', 'IMAX', 'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Thriller', 'War', 'Western']

# Function to create multi-hot encoding
def encode_genres(genres_str):
    if genres_str is None:
        genres_list = []
    else:
        genres_list = genres_str.split('|')
    return [1 if genre in genres_list else 0 for genre in all_genres]

# Apply encoding
movies = movies.with_columns(
    pl.col('genres').map_elements(encode_genres, return_dtype=pl.List(pl.Int64)).alias('genre_embedding')
)

# Select only movieId and genre_embedding
genre_df = movies.select(['movieId', 'genre_embedding'])

# Convert list to string for CSV
genre_df = genre_df.with_columns(
    pl.col('genre_embedding').map_elements(lambda x: '[' + ', '.join(map(str, x)) + ']', return_dtype=pl.Utf8)
)

# Save to CSV
genre_df.write_csv('../../data/ml-32m/genre.csv')

print("Genre preprocessing completed.")